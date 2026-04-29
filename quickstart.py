import argparse
import asyncio
import shutil
from getpass import getpass
from pathlib import Path

from openai import AsyncOpenAI

from add_bot_assistant import (
    BotDraft,
    DEFAULT_MESSAGE_FREQUENCY,
    bot_config_entry,
    generate_persona,
    update_env_file,
    upsert_bot_config,
    validate_bot_username,
    write_bot_config_file,
)
from generate_twitch_bot_tokens import get_token_for_account, set_env_value

DEFAULT_ENV_PATH = Path(".env")
DEFAULT_REDIRECT_URI = "http://localhost:3000/callback"
DEFAULT_TRANSCRIPT_ENDPOINT = "http://127.0.0.1:5001/latest-transcript"

MODEL_OPTIONS = [
    (
        "gpt-4o-mini",
        "recommended: fast, low cost, good for short Twitch chat lines",
    ),
    (
        "gpt-4o",
        "higher quality, higher cost",
    ),
    (
        "custom",
        "enter another OpenAI chat model name",
    ),
]


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{label}{suffix}: ").strip()
    return raw or default


def _prompt_secret(label: str, keep_existing: bool = False) -> str:
    hint = " (blank keeps existing)" if keep_existing else ""
    return getpass(f"{label}{hint}: ").strip()


def _prompt_bool(label: str, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    raw = input(f"{label} ({suffix}): ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "true", "1"}


def _env_has_value(env_path: Path, key: str) -> bool:
    if not env_path.exists():
        return False
    prefix = f"{key}="
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix) and line[len(prefix) :].strip():
            return True
    return False


def _ensure_env_file(env_path: Path) -> None:
    if env_path.exists():
        return
    example = Path(".env.example")
    if example.exists():
        shutil.copyfile(example, env_path)
    else:
        env_path.write_text("", encoding="utf-8")
    print(f"Created {env_path}.")


def _write_if_value(env_path: Path, key: str, value: str) -> None:
    if value:
        set_env_value(env_path, key, value)


def _choose_model(env_path: Path) -> str:
    print("\nOpenAI model options:")
    for i, (model, desc) in enumerate(MODEL_OPTIONS, start=1):
        print(f"{i}. {model} - {desc}")

    choice = _prompt("Choose model", "1")
    if choice.isdigit():
        index = int(choice)
        if 1 <= index <= len(MODEL_OPTIONS):
            model = MODEL_OPTIONS[index - 1][0]
            if model != "custom":
                set_env_value(env_path, "OPENAI_MODEL", model)
                return model

    model = _prompt("OpenAI model name", "gpt-4o-mini")
    set_env_value(env_path, "OPENAI_MODEL", model)
    return model


def _configure_core_env(env_path: Path) -> dict[str, str]:
    print("\nCore setup")
    existing_channel = _read_env_value(env_path, "TARGET_CHANNEL")
    default_channel = "" if existing_channel in {"", "your_channel"} else existing_channel
    target_channel = _prompt("Target Twitch channel login", default_channel)
    if not target_channel:
        raise RuntimeError("TARGET_CHANNEL is required.")
    set_env_value(env_path, "TARGET_CHANNEL", target_channel)

    preferred_name = _prompt("Broadcaster display name", target_channel)
    set_env_value(env_path, "PREFERRED_BROADCASTER_NAME", preferred_name)
    aliases = _prompt("Broadcaster aliases, comma-separated", f"{preferred_name},{target_channel}")
    set_env_value(env_path, "BROADCASTER_ALIASES", aliases)

    openai_key = _prompt_secret("OpenAI API key", keep_existing=_env_has_value(env_path, "OPENAI_API_KEY"))
    if openai_key:
        set_env_value(env_path, "OPENAI_API_KEY", openai_key)
    elif not _env_has_value(env_path, "OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for AI-generated bot roles.")

    model = _choose_model(env_path)

    twitch_client_id = _prompt_secret(
        "Twitch client ID",
        keep_existing=_env_has_value(env_path, "TWITCH_CLIENT_ID"),
    )
    if twitch_client_id:
        set_env_value(env_path, "TWITCH_CLIENT_ID", twitch_client_id)
    elif _env_has_value(env_path, "TWITCH_CLIENT_ID"):
        twitch_client_id = _read_env_value(env_path, "TWITCH_CLIENT_ID")
    else:
        raise RuntimeError("TWITCH_CLIENT_ID is required.")

    twitch_client_secret = _prompt_secret(
        "Twitch client secret",
        keep_existing=_env_has_value(env_path, "TWITCH_CLIENT_SECRET"),
    )
    if twitch_client_secret:
        set_env_value(env_path, "TWITCH_CLIENT_SECRET", twitch_client_secret)
    elif _env_has_value(env_path, "TWITCH_CLIENT_SECRET"):
        twitch_client_secret = _read_env_value(env_path, "TWITCH_CLIENT_SECRET")
    else:
        raise RuntimeError("TWITCH_CLIENT_SECRET is required.")

    return {
        "openai_key": openai_key or _read_env_value(env_path, "OPENAI_API_KEY"),
        "model": model,
        "twitch_client_id": twitch_client_id,
        "twitch_client_secret": twitch_client_secret,
    }


def _read_env_value(env_path: Path, key: str) -> str:
    if not env_path.exists():
        return ""
    prefix = f"{key}="
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def _configure_transcript(env_path: Path) -> None:
    print("\nSpeech/transcript setup")
    if _prompt_bool("Use Microsoft Azure Speech-to-Text", default=False):
        azure_key = _prompt_secret("Azure Speech key", keep_existing=_env_has_value(env_path, "AZURE_SPEECH_KEY"))
        _write_if_value(env_path, "AZURE_SPEECH_KEY", azure_key)
        region = _prompt("Azure Speech region", _read_env_value(env_path, "AZURE_SPEECH_REGION") or "westus")
        set_env_value(env_path, "AZURE_SPEECH_REGION", region)
    else:
        set_env_value(env_path, "AZURE_SPEECH_KEY", "")
        endpoint = _prompt("HTTP transcript endpoint", DEFAULT_TRANSCRIPT_ENDPOINT)
        set_env_value(env_path, "TRANSCRIPT_HTTP_ENDPOINT", endpoint)


async def _add_bots_loop(env_path: Path, settings: dict[str, str], redirect_uri: str, timeout_s: int) -> list[BotDraft]:
    drafts: list[BotDraft] = []
    existing_names: set[str] = set()
    ai_client = AsyncOpenAI(api_key=settings["openai_key"])
    squad_direction = _prompt("Overall vibe for the bot squad", "friendly, varied, low-spam Twitch chat")

    while _prompt_bool("Add a bot account", default=(not drafts)):
        raw_name = _prompt("Bot Twitch username")
        try:
            name = validate_bot_username(raw_name, existing_names=existing_names)
        except ValueError as exc:
            print(f"Invalid bot username: {exc}")
            continue

        token = await asyncio.to_thread(
            get_token_for_account,
            client_id=settings["twitch_client_id"],
            client_secret=settings["twitch_client_secret"],
            username=name,
            redirect_uri=redirect_uri,
            timeout_s=timeout_s,
        )

        direction = _prompt("What should this bot be like? Blank lets AI choose")
        descriptions = [f"{draft.name}: {draft.persona['description']}" for draft in drafts]
        persona = await generate_persona(
            ai_client,
            name,
            direction,
            squad_direction,
            descriptions,
            model=settings["model"],
        )
        if not persona:
            print("Persona generation failed; bot not added.")
            continue

        print("\nGenerated bot role:")
        print(f"- {persona['description']}")
        print(f"- tone: {persona['tone']}")
        print(f"- phrasing: {persona['phrasing']}")

        if not _prompt_bool("Use this bot", default=True):
            continue

        is_moderator = _prompt_bool("Is this bot a moderator", default=False)
        drafts.append(
            BotDraft(
                name=name,
                token=token,
                persona=persona,
                is_moderator=is_moderator,
                message_frequency=DEFAULT_MESSAGE_FREQUENCY,
            )
        )
        existing_names.add(name)

    return drafts


def _write_bot_files(env_path: Path, drafts: list[BotDraft], replace_bots: bool) -> None:
    for draft in drafts:
        update_env_file(draft.name, draft.token, env_path=env_path)

    if replace_bots:
        write_bot_config_file([bot_config_entry(draft) for draft in drafts])
        print("Replaced local bot list with quickstart bots.")
    else:
        for draft in drafts:
            upsert_bot_config(draft)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive first-run setup for bots-v2.")
    parser.add_argument("--env-path", default=".env", help="Env file to create/update")
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI, help="Twitch OAuth redirect URI")
    parser.add_argument("--timeout", type=int, default=180, help="OAuth timeout per bot in seconds")
    args = parser.parse_args()

    env_path = Path(args.env_path)
    _ensure_env_file(env_path)

    try:
        settings = _configure_core_env(env_path)
        _configure_transcript(env_path)
        drafts = await _add_bots_loop(env_path, settings, args.redirect_uri, args.timeout)
    except Exception as exc:
        print(f"Quickstart stopped: {exc}")
        return

    if drafts:
        replace_bots = _prompt_bool("Replace local bot list with only these bots", default=True)
        _write_bot_files(env_path, drafts, replace_bots=replace_bots)
    else:
        print("No bots added.")

    print("\nQuickstart complete.")
    print("Run `python runner.py --bot <botname> --smoketest --no-mic --no-helix` for a safe check.")


if __name__ == "__main__":
    asyncio.run(main())
