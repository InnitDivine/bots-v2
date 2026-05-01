from dataclasses import dataclass

from config import BOTS, TARGET_CHANNEL
from divbot_memory import DivBotMemory
from shared import SharedState


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    response: str = ""
    should_exit: bool = False


def parse_divbots_command(text: str) -> tuple[str, str] | None:
    raw = (text or "").strip()
    if not raw.lower().startswith("!divbots"):
        return None
    rest = raw[len("!divbots") :].strip()
    if not rest:
        return "status", ""
    name, _, arg = rest.partition(" ")
    return name.strip().lower(), arg.strip()


def apply_divbots_command(
    text: str,
    shared: SharedState,
    memory: DivBotMemory,
    *,
    local: bool = False,
) -> CommandResult:
    parsed = parse_divbots_command(text)
    if parsed is None:
        return CommandResult(False)

    cmd, arg = parsed
    if cmd == "status":
        quiet = "yes" if shared.is_quiet() else "no"
        return CommandResult(
            True,
            f"DivBots status: bots={len(BOTS)} channel={TARGET_CHANNEL} quiet={quiet} "
            f"game={shared.game or 'unknown'} hype={shared.hype} emotion={shared.detected_emotion} "
            f"blocked={len(memory.blocked_phrases())}",
        )
    if cmd == "quiet":
        seconds = _duration_seconds(arg, default=300)
        shared.set_quiet(seconds)
        return CommandResult(True, f"DivBots quiet for {int(seconds)}s")
    if cmd == "resume":
        shared.resume()
        return CommandResult(True, "DivBots resumed")
    if cmd == "stop":
        shared.stop_cast()
        return CommandResult(True, "DivBots stopped", should_exit=local)
    if cmd == "topic":
        if not arg:
            return CommandResult(True, "Usage: !divbots topic <topic>")
        shared.set_topic(arg)
        return CommandResult(True, "DivBots topic set")
    if cmd == "hype":
        shared.boost_hype()
        return CommandResult(True, "DivBots hype queued")
    if cmd == "reload":
        shared.refresh_meta()
        return CommandResult(True, "DivBots reloaded shared state")
    if cmd == "forget":
        if not arg:
            return CommandResult(True, "Usage: !divbots forget <text/topic>")
        memory.forget_topic(arg)
        return CommandResult(True, "DivBots forgot that topic")
    if cmd == "block":
        if not arg:
            return CommandResult(True, "Usage: !divbots block <phrase>")
        memory.block_phrase(arg)
        return CommandResult(True, "DivBots blocked phrase")
    return CommandResult(True, "DivBots commands: status quiet resume stop topic hype reload forget block")


def _duration_seconds(text: str, default: int = 300) -> int:
    raw = (text or "").strip().lower()
    if not raw:
        return default
    try:
        return max(1, min(43_200, int(raw)))
    except ValueError:
        pass
    if raw.endswith("m") and raw[:-1].isdigit():
        return max(1, min(43_200, int(raw[:-1]) * 60))
    if raw.endswith("h") and raw[:-1].isdigit():
        return max(1, min(43_200, int(raw[:-1]) * 3600))
    return default
