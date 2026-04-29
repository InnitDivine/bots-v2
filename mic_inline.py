import asyncio
import json
from typing import Callable, Optional

import aiohttp

from config import (
    AZURE_SPEECH_KEY,
    AZURE_SPEECH_REGION,
    MIC_MIN_CHARS,
    MIC_MIN_WORDS,
    TRANSCRIPT_HTTP_ENDPOINT,
    TRANSCRIPT_HTTP_TO_S,
)


class InlineMic:
    """Mic input source: Azure continuous STT, fallback HTTP transcript polling."""

    def __init__(self, session: aiohttp.ClientSession, on_text_cb: Callable[[str, bool], None]):
        self.sess = session
        self.on_text = on_text_cb
        self.mode = "azure" if (AZURE_SPEECH_KEY and AZURE_SPEECH_REGION) else "http"
        self.azure_recognizer = None
        self._http_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._last_final = ""

    def _passes_gate(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        if len(t) < MIC_MIN_CHARS:
            return False
        if len(t.split()) < MIC_MIN_WORDS:
            return False
        return True

    def _publish_final(self, text: str):
        t = (text or "").strip()
        if not self._passes_gate(t):
            return
        if t == self._last_final:
            return
        self._last_final = t
        self.on_text(t, True)

    async def start(self):
        self._stop_event.clear()

        if self.mode == "azure":
            try:
                import azure.cognitiveservices.speech as speechsdk

                speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
                speech_config.speech_recognition_language = "en-US"
                audio_config = speechsdk.audio.AudioConfig(use_default_microphone=True)
                self.azure_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

                def on_recognized(evt):
                    txt = (evt.result.text or "").strip()
                    if txt:
                        self._publish_final(txt)

                self.azure_recognizer.recognized.connect(on_recognized)
                self.azure_recognizer.start_continuous_recognition()
                print("mic mode: Azure continuous STT")
                return
            except Exception as e:
                print(f"azure STT init failed, falling back to HTTP: {e}")
                self.mode = "http"

        if self.mode == "http":
            print(f"mic mode: HTTP poll -> {TRANSCRIPT_HTTP_ENDPOINT}")
            if self._http_task is None or self._http_task.done():
                self._http_task = asyncio.create_task(self._http_loop(), name="inline_mic_http")

    async def _http_loop(self):
        while not self._stop_event.is_set():
            try:
                async with self.sess.get(TRANSCRIPT_HTTP_ENDPOINT, timeout=TRANSCRIPT_HTTP_TO_S) as r:
                    if r.status == 200:
                        raw = await r.text()
                        try:
                            js = json.loads(raw)
                            text = (js.get("text") or js.get("transcript") or js.get("message") or "").strip()
                        except Exception:
                            text = (raw or "").strip()
                        self._publish_final(text)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=0.6)
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        self._stop_event.set()

        if self._http_task is not None:
            self._http_task.cancel()
            await asyncio.gather(self._http_task, return_exceptions=True)
            self._http_task = None

        if self.azure_recognizer is not None:
            try:
                fut = self.azure_recognizer.stop_continuous_recognition_async()
                if hasattr(fut, "get"):
                    await asyncio.to_thread(fut.get)
            except Exception:
                pass
