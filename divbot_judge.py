import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from config import DIVBOTS_JUDGE_MODEL, DIVBOTS_USE_JUDGE


@dataclass(frozen=True)
class JudgeResult:
    passed: bool
    reason: str = "judge disabled"


def judge_enabled() -> bool:
    return DIVBOTS_USE_JUDGE


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


async def judge_message(
    ai_client,
    *,
    persona: dict[str, Any],
    mode: str,
    message: str,
    recent_context: str = "",
    timeout_s: float = 6.0,
) -> JudgeResult:
    if not DIVBOTS_USE_JUDGE:
        return JudgeResult(True, "judge disabled")
    prompt = (
        "Judge one outgoing Twitch bot line. Return only JSON: "
        '{"pass":true|false,"reason":"short"}.\n'
        "Pass only if line is short, non-spammy, on-topic, in persona, and not claiming to be human.\n"
        f"mode: {mode}\n"
        f"persona: {json.dumps(persona, ensure_ascii=True)[:600]}\n"
        f"recent_context: {recent_context[:500]}\n"
        f"line: {message[:240]}"
    )
    try:
        resp = await asyncio.wait_for(
            ai_client.chat.completions.create(
                model=DIVBOTS_JUDGE_MODEL,
                messages=[
                    {"role": "system", "content": "You are a strict Twitch chat safety judge."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=40,
                temperature=0.0,
            ),
            timeout=timeout_s,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = _extract_json(raw)
        passed = bool(data.get("pass"))
        reason = str(data.get("reason") or ("passed" if passed else "failed")).strip()[:80]
        return JudgeResult(passed, reason)
    except asyncio.CancelledError:
        raise
    except Exception:
        return JudgeResult(False, "judge error")
