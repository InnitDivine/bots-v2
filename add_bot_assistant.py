"""
Assist with adding one or more Twitch bot accounts.

Secrets are written only to .env. Tokens are never printed.
"""

import argparse
import ast
import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from openai import AsyncOpenAI

from config import BOTS, OPENAI_API_KEY, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, normalize_twitch_token
from generate_twitch_bot_tokens import get_token_for_account

USERNAME_RE = re.compile(r"^[a-z0-9_]{4,25}$")
DEFAULT_MESSAGE_FREQUENCY = (45, 95)
DEFAULT_REDIRECT_URI = "http://localhost:3000/callback"


@dataclass
class BotDraft:
    name: str
    token: str
    persona: dict[str, Any]
    is_moderator: bool
    message_frequency: tuple[int, int] = DEFAULT_MESSAGE_FREQUENCY


def validate_bot_username(username: str, existing_names: set[str] | None = None) -> str:
    name = (username or "").strip().lower()
    if not USERNAME_RE.fullmatch(name):
        raise ValueError("Bot username must be 4-25 lowercase letters, numbers, or underscores.")
    if existing_names and name in existing_names:
        raise ValueError(f"Bot '{name}' already exists.")
    return name


def validate_persona(persona: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(persona, dict):
        raise ValueError("Persona must be a JSON object.")

    description = str(persona.get("description") or "").strip()
    tone = str(persona.get("tone") or "").strip()
    phrasing = str(persona.get("phrasing") or "").strip()
    try:
        llm_temp = float(persona.get("llm_temp"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Persona llm_temp must be a number.") from exc

    if not description:
        raise ValueError("Persona description is required.")
    if not tone:
        raise ValueError("Persona tone is required.")
    if not phrasing:
        raise ValueError("Persona phrasing is required.")
    if not 0.6 <= llm_temp <= 1.0:
        raise ValueError("Persona llm_temp must be between 0.6 and 1.0.")

    return {
        "description": description,
        "tone": tone,
        "phrasing": phrasing,
        "llm_temp": llm_temp,
    }


def validate_persona_json(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Persona must be valid JSON.") from exc
    return validate_persona(parsed)


def _assignment_node(path: Path, name: str) -> ast.Assign:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            if node.end_lineno is None:
                raise ValueError(f"Cannot locate end of {name} assignment in {path}.")
            return node
    raise ValueError(f"Cannot find {name} assignment in {path}.")


def _insert_before_assignment_end(path: Path, assignment_name: str, entry_lines: list[str]) -> None:
    node = _assignment_node(path, assignment_name)
    lines = path.read_text(encoding="utf-8").splitlines()
    insert_at = node.end_lineno - 1
    updated = lines[:insert_at] + entry_lines + lines[insert_at:]
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")


def _prompt_bool(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{label} ({suffix}): ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "true", "1"}


def _prompt_usernames(count: int | None = None) -> list[str]:
    if count is not None:
        names: list[str] = []
        for i in range(1, count + 1):
            names.append(input(f"Bot {i} Twitch username: ").strip())
        return names

    raw = input("Enter bot Twitch usernames, comma-separated: ").strip()
    if raw:
        return [name.strip() for name in raw.split(",") if name.strip()]

    names = []
    print("Enter one bot username per line. Leave blank when done.")
    while True:
        name = input(f"Bot {len(names) + 1} Twitch username: ").strip()
        if not name:
            break
        names.append(name)
    return names


async def get_bot_token(
    bot_username: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    timeout_s: int,
) -> Optional[str]:
    try:
        return await asyncio.to_thread(
            get_token_for_account,
            client_id=client_id,
            client_secret=client_secret,
            username=bot_username,
            redirect_uri=redirect_uri,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        print(f"Token generation failed for {bot_username}: {exc}")
        return None


async def generate_persona(
    ai_client: AsyncOpenAI,
    bot_name: str,
    direction: str,
    squad_direction: str,
    existing_descriptions: list[str],
    model: str = "gpt-4o-mini",
) -> Optional[dict[str, Any]]:
    direction_text = direction or "No specific direction. Choose a useful distinct role for this bot."
    existing_text = "\n".join(f"- {line}" for line in existing_descriptions) or "none yet"
    prompt = f"""
You are configuring a Twitch multi-bot chat squad.

Bot username: {bot_name}
Overall squad/channel direction: {squad_direction or "friendly, varied Twitch chat support"}
User direction for this bot: {direction_text}
Existing bot roles:
{existing_text}

Generate one concise JSON object:
- "description": one short sentence describing what this bot will be in chat
- "tone": short attitude string
- "phrasing": short speaking style string
- "llm_temp": float from 0.6 to 1.0

Keep it distinct from existing bot roles. Keep it safe, natural, and Twitch-chat-sized.
"""

    print(f"\nGenerating AI persona for {bot_name}...")
    try:
        response = await ai_client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return only valid JSON for a safe Twitch bot persona."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.6,
        )
        persona_str = response.choices[0].message.content
        if not persona_str:
            return None
        return validate_persona_json(persona_str)
    except Exception as exc:
        print(f"Error during AI persona generation: {exc}")
        return None


def update_env_file(bot_name: str, token: str, env_path: Path | str = ".env") -> None:
    name = validate_bot_username(bot_name)
    safe_token = normalize_twitch_token(token)
    path = Path(env_path)
    token_var_name = f"TWITCH_BOT_TOKEN_{name.upper()}"
    username_var_name = f"TWITCH_BOT_USERNAME_{name.upper()}"

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefixes = (f"{token_var_name}=", f"{username_var_name}=")
    lines = [line for line in lines if not line.startswith(prefixes)]
    lines.append(f"{username_var_name}={name}")
    lines.append(f"{token_var_name}={safe_token}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Updated .env with {username_var_name} and {token_var_name}.")


def update_config_py(
    bot_name: str,
    is_moderator: bool,
    message_frequency: tuple[int, int],
    config_path: Path | str = "config.py",
) -> None:
    name = validate_bot_username(bot_name)
    low, high = int(message_frequency[0]), int(message_frequency[1])
    if low <= 0 or high < low:
        raise ValueError("message_frequency must be a positive (min, max) tuple.")

    entry = [
        "    {",
        f'        "name": {json.dumps(name)},',
        f'        "username": _env({json.dumps(f"TWITCH_BOT_USERNAME_{name.upper()}")}, {json.dumps(name)}),',
        f'        "token": _env({json.dumps(f"TWITCH_BOT_TOKEN_{name.upper()}")}),',
        f"        \"message_frequency\": ({low}, {high}),",
        f"        \"is_moderator\": {bool(is_moderator)},",
        "    },",
    ]
    _insert_before_assignment_end(Path(config_path), "BOTS", entry)
    print(f"Updated config.py with {name}.")


def update_bot_persona_py(
    bot_name: str,
    persona: dict[str, Any],
    persona_path: Path | str = "bot_persona.py",
) -> None:
    name = validate_bot_username(bot_name)
    safe = validate_persona(persona)
    entry = [
        f"    {json.dumps(name)}: {{",
        f'        "description": {json.dumps(safe["description"])},',
        f'        "tone": {json.dumps(safe["tone"])},',
        f'        "phrasing": {json.dumps(safe["phrasing"])},',
        f'        "llm_temp": {safe["llm_temp"]:.3g},',
        "    },",
    ]
    _insert_before_assignment_end(Path(persona_path), "PERSONA_STYLE", entry)
    print(f"Updated bot_persona.py with {name}.")


def apply_bot_draft(draft: BotDraft, env_path: Path | str = ".env") -> None:
    update_env_file(draft.name, draft.token, env_path=env_path)
    update_config_py(draft.name, draft.is_moderator, draft.message_frequency)
    update_bot_persona_py(draft.name, draft.persona)


async def collect_bot_drafts(args: argparse.Namespace) -> list[BotDraft]:
    existing = {str(b.get("name", "")).strip().lower() for b in BOTS}
    raw_names = _prompt_usernames(args.count)
    if not raw_names:
        print("No bot usernames entered.")
        return []

    names: list[str] = []
    for raw_name in raw_names:
        try:
            name = validate_bot_username(raw_name, existing_names=existing | set(names))
        except ValueError as exc:
            print(f"Invalid bot username '{raw_name}': {exc}")
            return []
        names.append(name)

    squad_direction = input("Overall vibe for this bot squad (optional): ").strip()
    ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    drafts: list[BotDraft] = []
    descriptions: list[str] = []

    for index, name in enumerate(names, start=1):
        print("\n" + "-" * 50)
        print(f"Setting up bot {index}/{len(names)}: {name}")

        token = await get_bot_token(name, args.redirect_uri, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, args.timeout)
        if not token:
            print("Failed to get token. Aborting before writing any new bot config.")
            return []

        direction = input("Describe what this bot should be like, or press Enter for AI to choose: ").strip()
        persona = await generate_persona(ai_client, name, direction, squad_direction, descriptions, model=args.model)
        if not persona:
            print("Failed to generate persona. Aborting before writing any new bot config.")
            return []

        print("\nGenerated role:")
        print(f"- {persona['description']}")
        print(f"- tone: {persona['tone']}")
        print(f"- phrasing: {persona['phrasing']}")
        print(f"- llm_temp: {persona['llm_temp']}")

        if not _prompt_bool("Use this persona", default=True):
            print("Aborting before writing any new bot config.")
            return []

        is_moderator = _prompt_bool("Is this bot a moderator", default=False)
        draft = BotDraft(name=name, token=token, persona=persona, is_moderator=is_moderator)
        drafts.append(draft)
        descriptions.append(f"{name}: {persona['description']}")

    return drafts


async def main() -> None:
    parser = argparse.ArgumentParser(description="Add one or more Twitch bot accounts.")
    parser.add_argument("--count", type=int, default=None, help="Number of bots to add")
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI, help="Twitch OAuth redirect URI")
    parser.add_argument("--timeout", type=int, default=180, help="OAuth timeout per bot in seconds")
    parser.add_argument("--env-path", default=".env", help="Env file to update")
    parser.add_argument("--model", default="gpt-4o-mini", help="OpenAI model for persona generation")
    args = parser.parse_args()

    if not OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY is not set.")
        return
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        print("Error: TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set.")
        return
    if args.count is not None and args.count <= 0:
        print("Error: --count must be greater than 0.")
        return

    print("--- Multi-Bot Assistant ---")
    print("Tokens will be written to .env and will not be printed.")
    drafts = await collect_bot_drafts(args)
    if not drafts:
        return

    print("\nReady to write these bots:")
    for draft in drafts:
        mod = "moderator" if draft.is_moderator else "chatter"
        print(f"- {draft.name}: {draft.persona['description']} ({mod})")

    if not _prompt_bool("Write .env, config.py, and bot_persona.py now", default=True):
        print("No files changed.")
        return

    for draft in drafts:
        apply_bot_draft(draft, env_path=args.env_path)

    print("\n" + "=" * 50)
    print(f"Bot setup complete. Added {len(drafts)} bot(s).")
    print("Run `python launch_multi.py` to include them.")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
