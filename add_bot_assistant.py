"""
Assist with adding a Twitch bot account.

Secrets are written only to .env. Tokens are never printed.
"""

import ast
import asyncio
import json
import re
import webbrowser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from openai import AsyncOpenAI

from bot_persona import PERSONA_STYLE
from config import BOTS, OPENAI_API_KEY, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, normalize_twitch_token

USERNAME_RE = re.compile(r"^[a-z0-9_]{4,25}$")
DEFAULT_MESSAGE_FREQUENCY = (45, 95)


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

    tone = str(persona.get("tone") or "").strip()
    phrasing = str(persona.get("phrasing") or "").strip()
    try:
        llm_temp = float(persona.get("llm_temp"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Persona llm_temp must be a number.") from exc

    if not tone:
        raise ValueError("Persona tone is required.")
    if not phrasing:
        raise ValueError("Persona phrasing is required.")
    if not 0.6 <= llm_temp <= 1.0:
        raise ValueError("Persona llm_temp must be between 0.6 and 1.0.")

    return {"tone": tone, "phrasing": phrasing, "llm_temp": llm_temp}


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


async def get_bot_token(bot_username: str, redirect_uri: str, client_id: str) -> Optional[str]:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "token",
        "scope": "chat:read chat:edit",
    }
    auth_url = f"https://id.twitch.tv/oauth2/authorize?{urlencode(params)}"

    print("\n" + "=" * 50)
    print("Twitch Authentication")
    print("=" * 50)
    print("1. A browser window will open for Twitch authorization.")
    print(f"2. Log in as bot account: '{bot_username}'")
    print("3. After approval, copy the full redirected URL.")

    webbrowser.open(auth_url)
    redirect_url = input("Paste the full redirect URL here: ").strip()
    parsed = urlparse(redirect_url)
    params = parse_qs(parsed.fragment or parsed.query)
    token = (params.get("access_token") or [""])[0]
    if not token:
        print("Error: could not find access_token in the URL.")
        return None
    try:
        return normalize_twitch_token(token)
    except ValueError:
        print("Error: Twitch token format looked invalid.")
        return None


async def generate_persona(ai_client: AsyncOpenAI, bot_name: str, description: str) -> Optional[dict[str, Any]]:
    prompt = f"""
You are a configuration assistant for a Twitch bot system.
Generate a JSON object for bot '{bot_name}' from this description:
{description}

Required keys:
- "tone": short attitude string
- "phrasing": short speaking style string
- "llm_temp": float from 0.6 to 1.0
"""

    print("\nGenerating AI persona...")
    try:
        response = await ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Return only valid JSON for a Twitch bot persona."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.5,
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
    print("Updated config.py with the new bot.")


def update_bot_persona_py(
    bot_name: str,
    persona: dict[str, Any],
    persona_path: Path | str = "bot_persona.py",
) -> None:
    name = validate_bot_username(bot_name)
    safe = validate_persona(persona)
    entry = [
        f"    {json.dumps(name)}: {{",
        f'        "tone": {json.dumps(safe["tone"])},',
        f'        "phrasing": {json.dumps(safe["phrasing"])},',
        f'        "llm_temp": {safe["llm_temp"]:.3g},',
        "    },",
    ]
    _insert_before_assignment_end(Path(persona_path), "PERSONA_STYLE", entry)
    print("Updated bot_persona.py with the new persona.")


async def main() -> None:
    if not OPENAI_API_KEY:
        print("Error: OPENAI_API_KEY is not set.")
        return
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        print("Error: TWITCH_CLIENT_ID or TWITCH_CLIENT_SECRET is not set.")
        return

    print("--- New Bot Assistant ---")
    existing = {str(b.get("name", "")).strip().lower() for b in BOTS}
    bot_name_raw = input("Enter the new bot's Twitch username: ")
    try:
        bot_name = validate_bot_username(bot_name_raw, existing_names=existing)
    except ValueError as exc:
        print(f"Invalid bot username: {exc}")
        return

    token = await get_bot_token(bot_name, "http://localhost:3000", TWITCH_CLIENT_ID)
    if not token:
        print("Failed to get token. Aborting.")
        return

    description = input("Enter a brief description of the bot's personality: ")
    ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    persona = await generate_persona(ai_client, bot_name, description)
    if not persona:
        print("Failed to generate persona. Aborting.")
        return

    print("\n--- Generated Persona ---")
    print(json.dumps(persona, indent=2))
    print("--------------------------")

    is_mod_str = input("Is this bot a moderator? (y/n): ").lower().strip()
    is_moderator = is_mod_str == "y"

    print("\nUpdating configuration files...")
    update_env_file(bot_name, token)
    update_config_py(bot_name, is_moderator, DEFAULT_MESSAGE_FREQUENCY)
    update_bot_persona_py(bot_name, persona)

    print("\n" + "=" * 50)
    print("Bot setup complete.")
    print(f"'{bot_name}' has been added. Run `python launch_multi.py` to include it.")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
