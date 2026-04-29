import argparse
import asyncio
import logging
from contextlib import suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import aiohttp
from openai import AsyncOpenAI

from bot_persona import Bot, Persona
from config import (
    BOTS,
    SHARED_META_FILE,
    HEARTBEAT_DIR,
    LOG_DIR,
    OPENAI_API_KEY,
    SHARED_TRANSCRIPT_FILE,
    CROSSBOT_RECENT_FILE,
    TARGET_CHANNEL,
    validate_runtime,
)
from gamebank import GameBankManager
from helix import Helix
from mic_inline import InlineMic
from shared import SharedState

BANNER = """
============================================================
================ RUNNER v12.0 (Single-bot) =================
============================================================
"""

SHUTDOWN_TIMEOUT_S = 8.0


def _bot_by_name(name: str) -> dict[str, Any]:
    return next((b for b in BOTS if b["name"] == name), None) or _raise_unknown_bot(name)


def _raise_unknown_bot(name: str):
    raise ValueError(f"Unknown bot: {name}")


def _build_persona(bot_conf: dict[str, Any]) -> Persona:
    return Persona(
        bot_conf["name"],
        bot_conf["username"],
        bot_conf["token"],
        bot_conf["message_frequency"],
        bot_conf.get("is_moderator", False),
    )


def _build_shared_state() -> SharedState:
    return SharedState(SHARED_TRANSCRIPT_FILE, CROSSBOT_RECENT_FILE, SHARED_META_FILE)


def _setup_logging(bot_name: str):
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    log = logging.getLogger()
    log.setLevel(logging.INFO)
    for h in list(log.handlers):
        log.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(sh)

    fh = RotatingFileHandler(log_dir / f"{bot_name}.log", maxBytes=1_500_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)


async def _heartbeat_loop(stop_event: asyncio.Event, bot_name: str):
    hb_dir = Path(HEARTBEAT_DIR)
    hb_dir.mkdir(parents=True, exist_ok=True)
    hb_path = hb_dir / f"{bot_name}.heartbeat"
    while not stop_event.is_set():
        hb_path.write_text(str(asyncio.get_running_loop().time()), encoding="utf-8")
        await asyncio.sleep(5)


async def _stdin_inject_loop(stop_event: asyncio.Event, shared: SharedState):
    while not stop_event.is_set():
        try:
            line = await asyncio.to_thread(input, "inject> ")
        except EOFError:
            return
        if not line:
            continue
        if line.strip().lower() in {"quit", "exit"}:
            stop_event.set()
            return
        shared.on_text(line, final=True)


async def run_bot_supervisor(
    stop_event: asyncio.Event,
    bot_factory,
    sleep_fn=asyncio.sleep,
    shutdown_timeout_s: float = SHUTDOWN_TIMEOUT_S,
) -> None:
    retry = 3.0
    while not stop_event.is_set():
        bot = None
        try:
            bot = bot_factory()
            await bot.start()
            if not stop_event.is_set():
                logging.warning("bot exited unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("bot runtime error")
        finally:
            if bot is not None:
                with suppress(Exception):
                    await asyncio.wait_for(bot.close(), timeout=shutdown_timeout_s)

        if stop_event.is_set():
            break

        await sleep_fn(min(45.0, retry + retry * 0.2))
        retry = min(45.0, retry * 2.0)


async def smoketest(bot_conf: dict, send_message: bool = False) -> bool:
    session = None
    bot = None
    ok = True
    try:
        session = aiohttp.ClientSession()
        ai = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=12.0)
        shared = _build_shared_state()
        gb = GameBankManager()
        p = _build_persona(bot_conf)
        bot = Bot(p, ai, shared, gb)

        await bot.connect()
        await asyncio.sleep(1.5)
        ch = bot.get_channel(TARGET_CHANNEL)
        if ch is None and bot.connected_channels:
            ch = bot.connected_channels[0]
        if send_message and ch:
            await ch.send("Kappa")
        await asyncio.sleep(1.0)
    except Exception as e:
        ok = False
        logging.exception("smoketest failed for %s", bot_conf["name"])
        print(f"smoketest failed for {bot_conf['name']}: {e}")
    finally:
        if bot is not None:
            with suppress(Exception):
                await asyncio.wait_for(bot.close(), timeout=SHUTDOWN_TIMEOUT_S)
        if session is not None:
            with suppress(Exception):
                await session.close()
    return ok


async def main():
    print(BANNER)
    ap = argparse.ArgumentParser()
    ap.add_argument("--bot", choices=[b["name"] for b in BOTS], required=True)
    ap.add_argument("--smoketest", action="store_true")
    ap.add_argument("--send-smoketest-message", action="store_true")
    ap.add_argument("--no-mic", action="store_true")
    ap.add_argument("--no-helix", action="store_true")
    ap.add_argument("--inject-stdin", action="store_true", help="Allow manual transcript injection from terminal input")
    args = ap.parse_args()

    _setup_logging(args.bot)

    errors = validate_runtime(require_helix=(not args.no_helix and not args.smoketest))
    if errors:
        print("Configuration errors:")
        for e in errors:
            print(f" - {e}")
        return

    bot_conf = _bot_by_name(args.bot)
    print(f"selected bot: {bot_conf['name']}")
    print(
        f"flags: smoketest={args.smoketest}, no_mic={args.no_mic}, "
        f"no_helix={args.no_helix}, inject_stdin={args.inject_stdin}, "
        f"send_smoketest_message={args.send_smoketest_message}"
    )

    if args.smoketest:
        ok = await smoketest(bot_conf, send_message=args.send_smoketest_message)
        print("smoketest passed" if ok else "smoketest failed")
        return

    session = aiohttp.ClientSession()
    ai = AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=12.0)
    shared = _build_shared_state()
    gb = GameBankManager()
    helix = Helix(session)

    stop_event = asyncio.Event()
    tasks: list[asyncio.Task] = []
    mic = None

    try:
        if not args.no_mic:
            mic = InlineMic(session, shared.on_text)
            logging.info("starting mic")
            await mic.start()
        else:
            logging.info("mic disabled")

        shared.prune_recent_messages(ttl_s=120.0)

        if not args.no_helix:
            logging.info("booting Helix")
            _title, game = await helix.get_stream_info()
            if game:
                shared.set_game(game)
                gb.ensure(game)
                logging.info("detected game: %s", game)

            emotes = await helix.get_global_emote_names()
            shared.set_global_emotes(emotes)
            logging.info("global emotes loaded: %s", len(emotes))
        else:
            logging.info("helix disabled")

        def make_bot():
            return Bot(_build_persona(bot_conf), ai, shared, gb)

        async def helix_loop():
            while not stop_event.is_set():
                try:
                    _title, game = await helix.get_stream_info()
                    if game and game != shared.game:
                        shared.set_game(game)
                        gb.ensure(game)
                        logging.info("game changed -> %s", game)
                    await asyncio.sleep(90)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(10)

        tasks.append(asyncio.create_task(_heartbeat_loop(stop_event, bot_conf["name"]), name="heartbeat_loop"))
        if args.inject_stdin:
            tasks.append(asyncio.create_task(_stdin_inject_loop(stop_event, shared), name="stdin_inject"))

        if not args.no_helix:
            tasks.append(asyncio.create_task(helix_loop(), name="helix_loop"))
        tasks.append(asyncio.create_task(run_bot_supervisor(stop_event, make_bot), name="bot_supervisor"))

        await asyncio.gather(*tasks)
    finally:
        stop_event.set()
        for t in tasks:
            if not t.done():
                t.cancel()
        if tasks:
            with suppress(Exception):
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=SHUTDOWN_TIMEOUT_S)

        if mic is not None:
            with suppress(Exception):
                await asyncio.wait_for(mic.stop(), timeout=SHUTDOWN_TIMEOUT_S)

        with suppress(Exception):
            await session.close()


if __name__ == "__main__":
    asyncio.run(main())
