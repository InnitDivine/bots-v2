import argparse
import os
import subprocess
import sys

from config import BOTS


def bot_names() -> list[str]:
    return [b["name"] for b in BOTS]


def main():
    parser = argparse.ArgumentParser(description="Launch each bot in its own console window (Windows).")
    parser.add_argument("--bots", default=None, help="Comma-separated list (default: all)")
    parser.add_argument("--smoketest", action="store_true")
    parser.add_argument("--send-smoketest-message", action="store_true")
    parser.add_argument("--no-mic", action="store_true")
    parser.add_argument("--no-helix", action="store_true")
    parser.add_argument("--inject-stdin", action="store_true")
    parser.add_argument("--use-watchdog", action="store_true", help="Launch watchdog supervisor instead of direct windows")
    args = parser.parse_args()

    if args.bots:
        selected = [x.strip().lower() for x in args.bots.split(",") if x.strip()]
    else:
        selected = bot_names()

    unknown = [n for n in selected if n not in bot_names()]
    if unknown:
        raise ValueError(f"Unknown bot(s): {', '.join(unknown)}")

    cwd = os.path.abspath(os.path.dirname(__file__))
    runner = os.path.join(cwd, "runner.py")

    if args.use_watchdog:
        watchdog = os.path.join(cwd, "watchdog.py")
        cmd = [sys.executable, "-u", watchdog, "--bots", ",".join(selected)]
        if args.smoketest:
            cmd.append("--smoketest")
        if args.send_smoketest_message:
            cmd.append("--send-smoketest-message")
        if args.no_mic:
            cmd.append("--no-mic")
        if args.no_helix:
            cmd.append("--no-helix")
        if args.inject_stdin:
            cmd.append("--inject-stdin")
        subprocess.Popen(cmd, cwd=cwd, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010), shell=False)
        print(f"launched watchdog: {' '.join(cmd)}")
        return

    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)

    for i, name in enumerate(selected):
        cmd = [sys.executable, "-u", runner, "--bot", name]

        if args.smoketest:
            cmd.append("--smoketest")
        if args.send_smoketest_message:
            cmd.append("--send-smoketest-message")

        # First bot handles mic by default.
        if args.no_mic:
            cmd.append("--no-mic")
        elif i > 0:
            cmd.append("--no-mic")

        # First bot handles helix by default.
        if args.no_helix:
            cmd.append("--no-helix")
        elif i > 0:
            cmd.append("--no-helix")

        if args.inject_stdin and i == 0:
            cmd.append("--inject-stdin")

        subprocess.Popen(cmd, cwd=cwd, creationflags=creationflags, shell=False)
        print(f"launched {name}: {' '.join(cmd)}")


if __name__ == "__main__":
    main()
