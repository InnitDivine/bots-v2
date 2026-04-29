import argparse
import os
import subprocess
import sys
import time

from config import BOTS


def _valid_names() -> list[str]:
    return [b["name"] for b in BOTS]


def _spawn_runner(cwd: str, name: str, args) -> subprocess.Popen:
    runner = os.path.join(cwd, "runner.py")
    cmd = [sys.executable, "-u", runner, "--bot", name]

    if args.smoketest:
        cmd.append("--smoketest")

    if args.no_mic:
        cmd.append("--no-mic")
    elif name != args.primary:
        cmd.append("--no-mic")

    if args.no_helix:
        cmd.append("--no-helix")
    elif name != args.primary:
        cmd.append("--no-helix")

    if args.inject_stdin and name == args.primary:
        cmd.append("--inject-stdin")

    print(f"spawn: {name} -> {' '.join(cmd)}")
    return subprocess.Popen(cmd, cwd=cwd, shell=False)


def main():
    ap = argparse.ArgumentParser(description="Supervise bot runner processes and restart on exit.")
    ap.add_argument("--bots", default=",".join(_valid_names()))
    ap.add_argument("--primary", default="sienna", help="Primary bot handles mic/helix unless disabled")
    ap.add_argument("--smoketest", action="store_true")
    ap.add_argument("--no-mic", action="store_true")
    ap.add_argument("--no-helix", action="store_true")
    ap.add_argument("--inject-stdin", action="store_true")
    ap.add_argument("--restart-delay", type=float, default=5.0)
    args = ap.parse_args()

    selected = [x.strip().lower() for x in args.bots.split(",") if x.strip()]
    unknown = [n for n in selected if n not in _valid_names()]
    if unknown:
        raise ValueError(f"Unknown bots: {', '.join(unknown)}")
    if args.primary not in selected:
        args.primary = selected[0]

    cwd = os.path.abspath(os.path.dirname(__file__))
    procs: dict[str, subprocess.Popen] = {}

    for name in selected:
        procs[name] = _spawn_runner(cwd, name, args)

    try:
        while True:
            for name in selected:
                proc = procs[name]
                code = proc.poll()
                if code is None:
                    continue
                print(f"process exited: {name} code={code}; restarting in {args.restart_delay}s")
                time.sleep(max(0.5, args.restart_delay))
                procs[name] = _spawn_runner(cwd, name, args)
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("watchdog shutting down...")
    finally:
        for p in procs.values():
            try:
                p.terminate()
            except Exception:
                pass


if __name__ == "__main__":
    main()
