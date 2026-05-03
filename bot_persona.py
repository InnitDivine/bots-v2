import asyncio
import os
import random
import re
import time
from collections import deque
from datetime import datetime
from typing import Optional

from openai import AsyncOpenAI
from twitchio.ext import commands

from config import (
    BASE_MIN_COOLDOWN_SECS,
    BROADCASTER_ALIASES,
    DEFAULT_MESSAGE_MODES,
    DIVBOTS_ALLOW_BOT_TO_BOT,
    DIVBOTS_ASCII_ONLY,
    DIVBOTS_MAX_CAST_MESSAGES_PER_5_MIN,
    FALLBACK_QUESTIONS,
    GLOBAL_MAX_MSG_PER_MINUTE,
    GLOBAL_MIN_COOLDOWN_SECS,
    LLM_MAX_TOKENS,
    MAX_MSG_PER_MINUTE,
    OPENAI_MODEL,
    PREFERRED_BROADCASTER_NAME,
    REALISTIC_MESSAGES,
    BOTS,
    TARGET_CHANNEL,
    TYPING_PATTERNS,
)
from divbot_commands import apply_divbots_command
from divbot_judge import judge_enabled, judge_message
from divbot_memory import DivBotMemory
from divbot_policy import BotPolicyState, PolicyConfig, decide_send, mark_send, sanitize_message
from divbot_viewers import DivBotViewers
from gamebank import GameBankManager
from shared import SharedState

LURK_MIN_SEC = 60
LURK_MAX_SEC = 240
BACKGROUND_MIN_SILENCE = 45.0
BACKGROUND_LLM_PROB = 0.35
MAX_WORDS = 12
LLM_TIMEOUT_S = 10.0
RECENT_LOCAL_LINES = 8
RECENT_CHAT_CONTEXT = 12
RECENT_HINT_WINDOW = 6
RECENT_MIC_LINES = 8

STREAMER_ALIASES = BROADCASTER_ALIASES or [TARGET_CHANNEL or "broadcaster"]
PREFERRED_NAME = PREFERRED_BROADCASTER_NAME or STREAMER_ALIASES[0]

DEFAULT_PERSONA_STYLE = {
    "description": "natural Twitch chatter",
    "tone": "friendly, natural, chat-safe",
    "phrasing": "short Twitch chat lines",
    "llm_temp": 0.75,
}


def _style_from_bot(bot: dict) -> dict:
    persona = bot.get("persona") if isinstance(bot.get("persona"), dict) else {}
    style = dict(DEFAULT_PERSONA_STYLE)
    for key in ("description", "tone", "phrasing"):
        value = str(persona.get(key) or "").strip()
        if value:
            style[key] = value
    try:
        style["llm_temp"] = max(0.6, min(1.0, float(persona.get("llm_temp", style["llm_temp"]))))
    except (TypeError, ValueError):
        pass
    return style


PERSONA_STYLE = {bot["name"]: _style_from_bot(bot) for bot in BOTS}


def _persona_style(name: str) -> dict:
    return PERSONA_STYLE.get(name, DEFAULT_PERSONA_STYLE)


def _alias_text() -> str:
    aliases = list(dict.fromkeys([PREFERRED_NAME, *STREAMER_ALIASES, TARGET_CHANNEL]))
    return "/".join(alias for alias in aliases if alias)


def _swap_streamer_alias(rng: random.Random, msg: Optional[str]) -> Optional[str]:
    if not msg:
        return msg
    alias = PREFERRED_NAME if rng.random() < 0.7 else rng.choice(STREAMER_ALIASES)
    msg = re.sub(r"\bthe streamer\b", alias, msg, flags=re.IGNORECASE)
    msg = re.sub(r"\bstreamer\b", alias, msg, flags=re.IGNORECASE)
    return msg


def _norm_message(msg: str) -> str:
    msg = (msg or "").lower().strip()
    msg = re.sub(r"[^a-z0-9]+", " ", msg)
    return re.sub(r"\s+", " ", msg).strip()


def _is_callout_request(text: str) -> bool:
    t = _norm_message(text)
    if not t:
        return False
    triggers = [
        "who is in chat",
        "whos in chat",
        "roll call",
        "call out",
        "callout",
        "anyone in chat",
    ]
    return any(x in t for x in triggers)


class Persona:
    def __init__(
        self,
        name: str,
        username: str,
        token: str,
        message_frequency: tuple[int, int],
        is_moderator: bool = False,
        settings: Optional[dict] = None,
    ):
        self.name = name
        self.username = username
        self.token = token
        self.message_frequency = message_frequency
        self.is_moderator = bool(is_moderator)
        self.settings = settings or {}


class Bot(commands.Bot):
    def __init__(self, persona: Persona, ai: AsyncOpenAI, shared: SharedState, gb: GameBankManager):
        super().__init__(token=persona.token.strip(), prefix="!", initial_channels=[TARGET_CHANNEL], nick=persona.username)
        self.p = persona
        self.ai = ai
        self.shared = shared
        self.gb = gb
        self.memory = DivBotMemory()
        self.viewers = DivBotViewers()
        self.policy_config = PolicyConfig.from_env()
        self.policy_state = BotPolicyState()

        self.rng = random.Random(f"{self.p.name}-{os.getpid()}")
        self._last_sent_at = 0.0
        self._last_seen_mic_id = ""
        self._lurk_until = 0.0
        self._chat_task: Optional[asyncio.Task] = None
        self._local_closing = False
        self._sent_window: deque[float] = deque(maxlen=MAX_MSG_PER_MINUTE * 2)
        self._recent_local_lines: deque[str] = deque(maxlen=RECENT_LOCAL_LINES)
        self._recent_chat: deque[tuple[str, str, float]] = deque(maxlen=RECENT_CHAT_CONTEXT)
        self._recent_mic_lines: deque[str] = deque(maxlen=RECENT_MIC_LINES)
        self._bot_usernames = {b["username"].strip().lower() for b in BOTS if b.get("username")}
        self._last_seen_orchestrator_hint = ""
        self._judge_drop_count = 0

    def _setting(self, name: str, default):
        return self.p.settings.get(name, default)

    def _mode_allowed(self, mode: str) -> bool:
        if mode in {"idle_question", "game_question"} and not self._setting("can_prompt_streamer", True):
            return False
        if mode in {"streamer_followup", "hype_reaction", "fail_reaction"} and not self._setting(
            "can_react_to_transcript", True
        ):
            return False
        if mode == "chat_reply" and not self._setting("can_react_to_chat", True):
            return False
        modes = self._setting("message_modes", DEFAULT_MESSAGE_MODES)
        return mode in modes

    async def event_ready(self):
        print(f"{self.p.name} connected as {self.nick}")
        if self._chat_task is None or self._chat_task.done():
            self._chat_task = asyncio.create_task(self.chat_loop(), name=f"{self.p.name}_chat_loop")

    def _message_parts(self, message) -> tuple[str, str, str]:
        author = ""
        if message.author is not None and getattr(message.author, "name", None):
            author = str(message.author.name).strip()
        display_name = str(getattr(message.author, "display_name", None) or author).strip()
        text = (message.content or "").strip()
        return author, display_name, text

    async def _handle_divbots_command(self, message, author: str, text: str) -> bool:
        if not text.lower().startswith("!divbots"):
            return False
        if BOTS and self.p.name != BOTS[0]["name"]:
            return True
        if self._command_authorized(message, author):
            result = apply_divbots_command(text, self.shared, self.memory)
            if result.handled and result.response:
                ch = getattr(message, "channel", None)
                if ch is not None:
                    await ch.send(result.response)
        return True

    def _observe_real_chat(self, message, author: str, display_name: str, text: str) -> None:
        is_mod, is_broadcaster = self._author_roles(message, author)
        self.shared.mark_real_chat(author, display_name)
        self.viewers.observe_message(author, display_name, is_mod=is_mod, is_broadcaster=is_broadcaster)
        self._recent_chat.append((author, text, time.time()))

    async def event_message(self, message):
        # Read real channel chat context (cross-chat awareness).
        try:
            if message.echo:
                return
            author, display_name, text = self._message_parts(message)
            if not author or not text:
                return
            if await self._handle_divbots_command(message, author, text):
                return
            if author.lower() in self._bot_usernames and not DIVBOTS_ALLOW_BOT_TO_BOT:
                return
            self._observe_real_chat(message, author, display_name, text)
        except Exception:
            return

    def _command_authorized(self, message, author: str) -> bool:
        is_mod, is_broadcaster = self._author_roles(message, author)
        return is_mod or is_broadcaster

    def _author_roles(self, message, author: str) -> tuple[bool, bool]:
        if author.strip().lower() == TARGET_CHANNEL.lower():
            return False, True
        is_mod = False
        is_broadcaster = False
        author_obj = getattr(message, "author", None)
        for attr in ("is_mod", "mod"):
            is_mod = is_mod or bool(getattr(author_obj, attr, False))
        for attr in ("is_broadcaster", "broadcaster"):
            is_broadcaster = is_broadcaster or bool(getattr(author_obj, attr, False))
        tags = getattr(message, "tags", {}) or {}
        if isinstance(tags, dict):
            badges = str(tags.get("badges") or tags.get("badge-info") or "").lower()
            is_mod = is_mod or "moderator" in badges
            is_broadcaster = is_broadcaster or "broadcaster" in badges
        return is_mod, is_broadcaster

    def _ctx_for_mode(self, mode: str, fallback: str) -> str:
        if mode in {"hype_reaction", "emote_only"}:
            return "hype"
        if mode == "fail_reaction":
            return "fail"
        if mode in {"game_question", "idle_question", "streamer_followup", "chat_reply"}:
            return "question"
        return fallback

    def _viewer_callout(self) -> Optional[str]:
        line = self.viewers.helper_viewer_callout(
            self.shared.last_real_chat_login,
            exclude_logins={*self._bot_usernames, TARGET_CHANNEL.lower()},
        )
        return self._sanitize(line)

    def _judge_context(self) -> str:
        return (
            f"mic={self._mic_hint()}; chat={self._chat_hint()}; "
            f"recent_sent={self._recent_hint()}; game={self.shared.game or 'unknown'}"
        )

    async def _judge_ok(self, mode: str, message: str) -> bool:
        if not judge_enabled():
            return True
        result = await judge_message(
            self.ai,
            persona={"name": self.p.name, "persona": _persona_style(self.p.name), "settings": self.p.settings},
            mode=mode,
            message=message,
            recent_context=self._judge_context(),
        )
        if not result.passed:
            self._judge_drop_count += 1
            print(f"judge dropped {self.p.name} line: {result.reason}")
        return result.passed

    async def event_disconnect(self):
        if self._chat_task and not self._chat_task.done():
            self._chat_task.cancel()
            await asyncio.gather(self._chat_task, return_exceptions=True)
        self._chat_task = None

    async def close(self):
        if self._local_closing:
            return
        self._local_closing = True
        try:
            if self._chat_task and not self._chat_task.done():
                self._chat_task.cancel()
                await asyncio.gather(self._chat_task, return_exceptions=True)
            self._chat_task = None
            if getattr(getattr(self, "_closing", None), "set", None) is not None:
                await super().close()
        finally:
            self._local_closing = False

    def _adaptive_cooldown(self) -> float:
        base = float(BASE_MIN_COOLDOWN_SECS) * float(self._setting("cooldown_multiplier", 1.0))
        if self.shared.hype > 7:
            return max(8.0, base - 2.0)
        if self.shared.silence > 30:
            return min(20.0, base + 3.0)
        return base

    def _cooldown_ok(self) -> bool:
        return (time.time() - self._last_sent_at) >= self._adaptive_cooldown()

    def _rate_limit_ok(self) -> bool:
        now = time.time()
        while self._sent_window and now - self._sent_window[0] > 60.0:
            self._sent_window.popleft()
        return len(self._sent_window) < MAX_MSG_PER_MINUTE

    def _can_send_now(self) -> bool:
        return self._cooldown_ok() and self._rate_limit_ok()

    def _policy_ok(self, mode: str, message: str = "") -> bool:
        if self.shared.is_quiet() or not self._mode_allowed(mode):
            return False
        decision = decide_send(
            self.policy_config,
            self.policy_state,
            mode=mode,
            bot_cooldown_s=self._adaptive_cooldown(),
            per_bot_max_per_minute=MAX_MSG_PER_MINUTE,
            last_real_chat_at=self.shared.last_real_chat_at,
            is_duplicate=bool(message and (self._is_local_duplicate(message) or self.shared.is_global_duplicate(message))),
        )
        return decision.allowed

    def _mark_sent(self):
        self._last_sent_at = time.time()
        self._sent_window.append(self._last_sent_at)
        mark_send(self.policy_state, getattr(self, "_last_send_mode", "idle_question"), self._last_sent_at)

    def _lurk_active(self) -> bool:
        return time.time() < self._lurk_until

    def _maybe_enter_lurk(self):
        style = _persona_style(self.p.name)
        temp = float(style.get("llm_temp", DEFAULT_PERSONA_STYLE["llm_temp"]))
        p = 0.06 if self.p.is_moderator else 0.08 + max(0.0, temp - 0.75) * 0.12
        if self.rng.random() < p:
            dur = self.rng.randint(LURK_MIN_SEC, LURK_MAX_SEC)
            self._lurk_until = time.time() + dur

    def _emote_only(self) -> str:
        if not self._setting("can_use_emotes", True):
            return self._stock("casual")
        pool = self.shared.global_emotes or ["Kappa", "LUL", "PogChamp", "4Head", "TriHard", "SeemsGood"]
        style = self._setting("emote_style", "balanced")
        count = 2 if style == "heavy" and len(pool) > 1 and self.rng.random() < 0.35 else 1
        return " ".join(self.rng.choice(pool) for _ in range(count))

    def _stock(self, ctx: str) -> str:
        if ctx == "hype":
            msg = self.rng.choice(REALISTIC_MESSAGES["hype"])
        elif ctx == "fail":
            msg = self.rng.choice(REALISTIC_MESSAGES["fail"])
        elif ctx == "question":
            msg = self.rng.choice(REALISTIC_MESSAGES["question"])
        elif self.rng.random() < 0.3:
            msg = self.rng.choice(REALISTIC_MESSAGES["memes"])
        else:
            msg = self.rng.choice(REALISTIC_MESSAGES["casual"])

        if self.rng.random() < TYPING_PATTERNS.get("all_lowercase", 0.6):
            msg = msg.lower()
        elif self.rng.random() < TYPING_PATTERNS.get("all_caps", 0.10) and len(msg) < 24:
            msg = msg.upper()

        if self.rng.random() < TYPING_PATTERNS.get("no_punctuation", 0.75):
            msg = msg.replace("?", "").replace("!", "").replace(".", "")
        return msg

    def _game_question(self) -> str:
        bank = self.gb.get(self.shared.game)
        if bank and bank.get("questions"):
            return self.rng.choice(bank["questions"])
        return self.rng.choice(FALLBACK_QUESTIONS)

    def _direct_callout(self) -> str:
        name = PREFERRED_NAME or TARGET_CHANNEL or "chat"
        base = [
            f"{self.p.username} here {name}",
            f"im here {name}",
            f"{self.p.username} in chat",
            f"here with you {name}",
        ]
        msg = self.rng.choice(base)
        if self.rng.random() < 0.35:
            msg = f"{msg} {self._emote_only()}"
        return msg

    def _sanitize(self, msg: Optional[str]) -> Optional[str]:
        if not msg:
            return None
        msg = _swap_streamer_alias(self.rng, msg)
        msg = sanitize_message(
            msg,
            max_words=int(self._setting("max_words", MAX_WORDS)),
            ascii_only=DIVBOTS_ASCII_ONLY,
        )
        if not msg:
            return None
        if self.memory.is_blocked(msg):
            return None

        bank = self.gb.get(self.shared.game)
        if bank:
            for t in bank.get("ban_terms", []):
                if t.lower() in msg.lower():
                    return None
        return msg

    def _recent_hint(self) -> str:
        if not self._recent_local_lines:
            return "none"
        return " | ".join(list(self._recent_local_lines)[-RECENT_HINT_WINDOW:])

    def _chat_hint(self) -> str:
        if not self._setting("can_react_to_chat", True):
            return "none"
        if not self._recent_chat:
            return "none"
        rows = []
        for author, text, _ts in list(self._recent_chat)[-RECENT_HINT_WINDOW:]:
            rows.append(f"{author}: {text}")
        return " | ".join(rows)

    def _mic_hint(self) -> str:
        if not self._recent_mic_lines:
            return "none"
        return " | ".join(list(self._recent_mic_lines)[-RECENT_HINT_WINDOW:])

    def _gamebank_hint(self) -> str:
        bank = self.gb.get(self.shared.game) or {}
        topics = bank.get("topics") or []
        questions = bank.get("questions") or []
        t = ", ".join([str(x) for x in topics[:4]]) if topics else "none"
        q = ", ".join([str(x) for x in questions[:3]]) if questions else "none"
        return f"topics={t}; likely_questions={q}"

    def _record_local_line(self, message: str):
        self._recent_local_lines.append(message)

    def _is_local_duplicate(self, message: str) -> bool:
        norm = _norm_message(message)
        if not norm:
            return False
        return any(_norm_message(row) == norm for row in self._recent_local_lines)

    def _instruction_for_mode(self, mode: str, tone: str, phrasing: str, help_mode: bool) -> str:
        if mode == "hype_reaction":
            lead = "Write one excited Twitch reaction. 1-6 words."
        elif mode == "fail_reaction":
            lead = "Write one kind fail reaction. 1-6 words. No dunking."
        elif mode == "game_question":
            lead = "Write one short game question. 3-10 words."
        elif mode == "idle_question":
            lead = "Write one dead-chat prompt. 3-10 words."
        elif mode == "chat_reply":
            lead = "Write one brief chat-support reply. 3-10 words."
        elif help_mode:
            lead = "Write one supportive Twitch chat line. 3-10 words."
        else:
            lead = "Write one short Twitch reaction. 2-6 words."
        return (
            f"{lead} Tone: {tone}. Style: {phrasing}. "
            f"Address broadcaster as {_alias_text()}. "
            "Never say streamer. Never mention AI. Use Twitch global emote names only. No Unicode emoji."
        )

    async def _ai(self, source_text: str, help_mode: bool, mode: str) -> Optional[str]:
        g = self.shared.game or "the game"
        bank = self.gb.get(self.shared.game) or {"ban_terms": []}
        banned = ", ".join(bank.get("ban_terms", []))
        blocked = self.memory.avoid_text()

        style = _persona_style(self.p.name)
        role = str(style.get("description") or "").strip()
        tone = style["tone"]
        phrasing = style["phrasing"]
        base_temp = float(style["llm_temp"])

        instruction = self._instruction_for_mode(mode, tone, phrasing, help_mode)
        temp = max(0.65, base_temp - 0.08) if help_mode or mode in {"game_question", "chat_reply"} else base_temp
        quoted = source_text[-180:] if help_mode else source_text[-140:]

        if self.p.is_moderator:
            instruction += (
                " You are a moderator in this channel, so keep lines constructive and de-escalating."
            )
        if self.shared.manual_topic:
            instruction += f" Current streamer-approved topic: {self.shared.manual_topic}."

        prompt = (
            f"mode: {mode}\n"
            f"broadcaster words: '{quoted}'\n"
            f"game: {g}\n"
            f"bot role: {role or 'natural Twitch chatter'}\n"
            f"bot purpose: {self._setting('purpose', '')}\n"
            f"gamebank hints: {self._gamebank_hint()}\n"
            f"recent broadcaster context: {self._mic_hint()}\n"
            f"recent real chat context: {self._chat_hint()}\n"
            f"avoid terms: {banned}\n"
            f"blocked memory: {blocked}\n"
            f"recent lines to avoid repeating: {self._recent_hint()}\n"
            "Grounding rule: do not invent specifics. If unsure, ask a short clarifying question.\n"
            f"{instruction}"
        )

        try:
            resp = await asyncio.wait_for(
                self.ai.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a realistic Twitch chatter."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=LLM_MAX_TOKENS,
                    temperature=temp,
                ),
                timeout=LLM_TIMEOUT_S,
            )
            msg = (resp.choices[0].message.content or "").strip()
            msg = re.sub(r"\s+", " ", msg)
            return self._sanitize(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def _ai_background(self, mode: str = "idle_question") -> Optional[str]:
        g = self.shared.game or "the game"
        bank = self.gb.get(self.shared.game) or {}
        banned = ", ".join(bank.get("ban_terms") or [])
        blocked = self.memory.avoid_text()
        topic_hint = ""
        if bank.get("topics"):
            topic_hint = f" Touch: {self.rng.choice(bank['topics'])}."

        style = _persona_style(self.p.name)
        role = str(style.get("description") or "").strip()
        prompt = (
            f"Mode: {mode}. "
            f"Quiet stream moment in {g}. Write one short Twitch line (3-10 words). "
            f"Bot role: {role or 'natural Twitch chatter'}. "
            f"Bot purpose: {self._setting('purpose', '')}. "
            f"Tone: {style['tone']}. Style: {style['phrasing']}. "
            f"Address {_alias_text()}, never streamer. "
            f"Use Twitch global emote names only, no Unicode emoji. Never mention AI. "
            f"Avoid: {banned}.{topic_hint} "
            f"Blocked memory: {blocked}. "
            f"Avoid repeating: {self._recent_hint()}. "
            f"Recent real chat context: {self._chat_hint()}. "
            f"Recent broadcaster context: {self._mic_hint()}. "
            f"Gamebank hints: {self._gamebank_hint()}. "
            "Grounding rule: do not invent specifics."
        )
        if self.p.is_moderator:
            prompt += " Keep moderator energy: calm, positive, and chat-safe."

        try:
            resp = await asyncio.wait_for(
                self.ai.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a realistic Twitch chatter."},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=LLM_MAX_TOKENS,
                    temperature=float(style["llm_temp"]),
                ),
                timeout=LLM_TIMEOUT_S,
            )
            msg = (resp.choices[0].message.content or "").strip()
            msg = re.sub(r"\s+", " ", msg)
            return self._sanitize(msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    def _diversify(self, message: Optional[str], ctx: str, help_mode: bool) -> Optional[str]:
        if not message:
            return None
        if not self._is_local_duplicate(message) and not self.shared.is_global_duplicate(message):
            return message

        for _ in range(4):
            if help_mode and self.rng.random() < 0.45:
                alt = self._game_question()
            elif self.rng.random() < 0.35:
                alt = self._emote_only()
            else:
                alt = self._stock(ctx)
            alt = self._sanitize(alt)
            if alt and not self._is_local_duplicate(alt) and not self.shared.is_global_duplicate(alt):
                return alt
        return None

    async def _wait_for_chat_tick(self, wait: int) -> bool:
        new_mic_pending = False
        remaining = float(wait)
        while remaining > 0:
            step = 1.0 if remaining > 1.0 else remaining
            await asyncio.sleep(step)
            self.shared.tick(step)
            self.shared.refresh_transcript()
            self.shared.refresh_meta()
            remaining -= step
            hint = self.shared.next_speaker_for(self.p.name)
            if hint and str(hint.get("id") or "") != self._last_seen_orchestrator_hint and self._can_send_now():
                break
            if self.shared.latest_id and self.shared.latest_id != self._last_seen_mic_id and self._can_send_now():
                new_mic_pending = True
                break
        return new_mic_pending

    def _base_chat_context(self) -> tuple[str, str, bool, bool]:
        urgent = self.shared.help_mode or (self.shared.hype > 7)
        help_mode = self.shared.help_mode or (self.shared.silence > 16)
        if self.shared.detected_emotion == "hype":
            ctx = "hype"
            mode = "hype_reaction"
        elif self.shared.detected_emotion == "fail":
            ctx = "fail"
            mode = "fail_reaction"
        elif help_mode:
            ctx = "general"
            mode = "streamer_followup"
        elif self.rng.random() < 0.1:
            ctx = "question"
            mode = "game_question"
        else:
            ctx = "general"
            mode = "idle_question"
        return ctx, mode, help_mode, urgent

    def _lurk_should_skip(self, urgent: bool) -> bool:
        if not self._lurk_active():
            return False
        if urgent:
            self._lurk_until = 0.0
            return False
        return True

    def _hint_context(self, ctx: str, mode: str, help_mode: bool) -> tuple[bool, Optional[dict], str, str, str, bool]:
        active_hint = self.shared.active_next_speaker_hint()
        hint_for_me = self.shared.next_speaker_for(self.p.name)
        hint_id = str(hint_for_me.get("id") or "") if hint_for_me else ""
        if active_hint and not hint_for_me:
            return True, None, "", ctx, mode, help_mode
        if not hint_for_me or hint_id == self._last_seen_orchestrator_hint:
            return bool(hint_for_me), hint_for_me, hint_id, ctx, mode, help_mode
        hinted_mode = str(hint_for_me.get("mode") or "").strip()
        if hinted_mode:
            mode = hinted_mode
            ctx = self._ctx_for_mode(mode, ctx)
            help_mode = mode in {"streamer_followup", "game_question", "chat_reply"}
        return False, hint_for_me, hint_id, ctx, mode, help_mode

    def _transcript_send_ready(self, mode: str, new_mic_pending: bool, hinted: bool) -> bool:
        transcript_ready = self.shared.latest_text and self.shared.latest_id
        new_line = self.shared.latest_id != self._last_seen_mic_id
        transcript_mode = mode in {"hype_reaction", "fail_reaction", "streamer_followup", "game_question"}
        if not (transcript_ready and transcript_mode and self._can_send_now() and self._policy_ok(mode)):
            return False
        return bool(new_mic_pending or new_line or hinted)

    async def _generate_transcript_message(self, mode: str, ctx: str, help_mode: bool) -> Optional[str]:
        generated = self._direct_callout() if _is_callout_request(self.shared.latest_text) else None
        if generated is None:
            generated = await self._ai(self.shared.latest_text, help_mode=help_mode, mode=mode)
        if not generated and (help_mode or ctx == "question"):
            generated = self._game_question()
        return generated or (self._emote_only() if self.rng.random() < 0.3 else self._stock(ctx))

    async def _transcript_message(self, mode: str, ctx: str, help_mode: bool, new_mic_pending: bool, hinted: bool) -> Optional[str]:
        if not self._transcript_send_ready(mode, new_mic_pending, hinted):
            return None
        new_line = self.shared.latest_id != self._last_seen_mic_id
        if new_line:
            self._last_seen_mic_id = self.shared.latest_id
        self._recent_mic_lines.append(self.shared.latest_text)
        return await self._generate_transcript_message(mode, ctx, help_mode)

    async def _hint_message(self, mode: str) -> Optional[str]:
        if not (self._can_send_now() and self._policy_ok(mode)):
            return None
        if mode == "chat_reply":
            return self._viewer_callout() or await self._ai_background(mode=mode)
        if mode == "emote_only":
            return self._emote_only()
        if mode == "game_question":
            return self._game_question()
        if mode == "idle_question":
            return await self._ai_background(mode=mode) or self._stock("question")
        return None

    async def _fallback_message(self, mode: str, ctx: str) -> tuple[Optional[str], str]:
        if not self._can_send_now():
            return None, mode
        if self.shared.silence >= BACKGROUND_MIN_SILENCE:
            mode = "idle_question"
            if self._policy_ok(mode) and self.rng.random() < BACKGROUND_LLM_PROB:
                return await self._ai_background(mode=mode), mode
        if self.shared.hype > 7:
            mode = "emote_only"
            if self._policy_ok(mode):
                self.shared.hype = 5
                return self._emote_only(), mode
        if self.rng.random() < 0.08:
            mode = "emote_only" if self.rng.random() < 0.3 else "chat_reply"
            if self._policy_ok(mode):
                return self._stock(ctx), mode
        return None, mode

    async def _compose_message(
        self,
        mode: str,
        ctx: str,
        help_mode: bool,
        new_mic_pending: bool,
        hint_for_me: Optional[dict],
    ) -> tuple[Optional[str], str]:
        message = await self._transcript_message(mode, ctx, help_mode, new_mic_pending, bool(hint_for_me))
        if hint_for_me and not message:
            message = await self._hint_message(mode)
        if not message:
            message, mode = await self._fallback_message(mode, ctx)
        return message, mode

    async def _finalize_message(self, message: Optional[str], mode: str, ctx: str, help_mode: bool) -> Optional[str]:
        message = self._sanitize(message)
        message = self._diversify(message, ctx=ctx, help_mode=help_mode)
        if message and not self._policy_ok(mode, message):
            return None
        if message and not await self._judge_ok(mode, message):
            return None
        return message

    def _mark_hint_seen(self, hint_id: str) -> None:
        if hint_id:
            self._last_seen_orchestrator_hint = hint_id
            self.shared.clear_next_speaker_hint(hint_id)

    async def _send_message(self, message: Optional[str], mode: str, ctx: str, help_mode: bool, hint_id: str) -> None:
        if not message:
            self._mark_hint_seen(hint_id)
            return
        ch = self.connected_channels[0] if self.connected_channels else self.get_channel(TARGET_CHANNEL)
        reserved = False
        if ch is not None:
            reserved = self.shared.try_remember_global_message(
                self.p.name,
                message,
                max_per_minute=GLOBAL_MAX_MSG_PER_MINUTE,
                min_interval_s=GLOBAL_MIN_COOLDOWN_SECS,
                max_per_window=DIVBOTS_MAX_CAST_MESSAGES_PER_5_MIN,
                window_s=300.0,
            )
        if ch is not None and reserved:
            await ch.send(message)
            self._last_send_mode = mode
            self._mark_sent()
            self._record_local_line(message)
            tag = "HELP" if help_mode else "NORMAL"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.p.name} -> '{message}' [{tag}/{ctx.upper()}]")
        self._mark_hint_seen(hint_id)

    async def chat_loop(self):
        await asyncio.sleep(self.rng.uniform(3, 8))
        print(f"{self.p.name} chat loop started")

        while True:
            try:
                minw, maxw = self.p.message_frequency
                new_mic_pending = await self._wait_for_chat_tick(self.rng.randint(minw, maxw))
                ctx, mode, help_mode, urgent = self._base_chat_context()
                if self._lurk_should_skip(urgent):
                    continue

                skip, hint_for_me, hint_id, ctx, mode, help_mode = self._hint_context(ctx, mode, help_mode)
                if skip:
                    continue

                message, mode = await self._compose_message(mode, ctx, help_mode, new_mic_pending, hint_for_me)
                message = await self._finalize_message(message, mode, ctx, help_mode)
                await self._send_message(message, mode, ctx, help_mode, hint_id)

                if not urgent and not self._lurk_active():
                    self._maybe_enter_lurk()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"chat_loop error ({self.p.name}): {e}")
                await asyncio.sleep(5)
