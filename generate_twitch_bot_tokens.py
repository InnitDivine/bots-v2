import argparse
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from config import BOTS, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, normalize_twitch_token

AUTH_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
SCOPES = ["chat:read", "chat:edit"]


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    code: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None
    done = threading.Event()

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path not in {"/callback", "/"}:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        qs = parse_qs(parsed.query)
        OAuthCallbackHandler.code = (qs.get("code") or [None])[0]
        OAuthCallbackHandler.state = (qs.get("state") or [None])[0]
        OAuthCallbackHandler.error = (qs.get("error") or [None])[0]
        OAuthCallbackHandler.done.set()

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h3>Auth complete.</h3><p>You can close this tab and return to the terminal.</p></body></html>"
        )


def _post_json(url: str, data: dict) -> dict:
    body = urlencode(data).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def exchange_code_for_token(client_id: str, client_secret: str, code: str, redirect_uri: str) -> str:
    data = _post_json(
        TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    token = (data.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("No access_token in token response.")
    return normalize_twitch_token(token)


def wait_for_callback(server: HTTPServer, timeout_s: int) -> tuple[Optional[str], Optional[str], Optional[str]]:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while not OAuthCallbackHandler.done.is_set() and time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        server.timeout = min(1.0, max(0.05, remaining))
        server.handle_request()
    if not OAuthCallbackHandler.done.is_set():
        return None, None, "timeout"
    return OAuthCallbackHandler.code, OAuthCallbackHandler.state, OAuthCallbackHandler.error


def validate_returned_state(expected_state: str, returned_state: Optional[str], username: str) -> None:
    if returned_state != expected_state:
        raise RuntimeError(f"State mismatch for {username}.")


def set_env_value(env_path: Path, key: str, value: str):
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")
    text = env_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    replaced = False
    out: list[str] = []
    for ln in lines:
        if ln.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"{key}={value}")
    env_path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def get_token_for_account(
    *,
    client_id: str,
    client_secret: str,
    username: str,
    redirect_uri: str,
    timeout_s: int,
) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
        raise ValueError("redirect_uri must be loopback http URI like http://127.0.0.1:8765/callback")

    OAuthCallbackHandler.code = None
    OAuthCallbackHandler.state = None
    OAuthCallbackHandler.error = None
    OAuthCallbackHandler.done.clear()

    server = HTTPServer((parsed.hostname, parsed.port), OAuthCallbackHandler)
    try:
        state = secrets.token_urlsafe(24)
        scope = " ".join(SCOPES)

        auth_query = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "state": state,
            "force_verify": "true",
            "login": username,
        }
        url = f"{AUTH_URL}?{urlencode(auth_query)}"
        print(f"\nAuthorizing account: {username}")
        print("Browser will open. Sign in to this specific account and approve access.")
        print(url)
        webbrowser.open(url, new=2)

        code, returned_state, error = wait_for_callback(server, timeout_s)

        if error:
            raise RuntimeError(f"OAuth error for {username}: {error}")
        if not code:
            raise RuntimeError(f"No auth code received for {username}.")
        validate_returned_state(state, returned_state, username)

        return exchange_code_for_token(client_id, client_secret, code, redirect_uri)
    finally:
        server.server_close()


def main():
    ap = argparse.ArgumentParser(description="Generate fresh Twitch chat OAuth tokens for bot accounts.")
    ap.add_argument(
        "--redirect-uri",
        default="http://localhost:3000/callback",
        help="Must be registered in your Twitch app settings.",
    )
    ap.add_argument("--timeout", type=int, default=180, help="Per-account OAuth timeout in seconds.")
    ap.add_argument("--write-env", action="store_true", help="Write generated tokens to .env")
    ap.add_argument("--env-path", default=".env", help="Path to env file when using --write-env")
    ap.add_argument("--extra-username", default="", help="Optional extra Twitch username (e.g. main account)")
    ap.add_argument(
        "--extra-env-key",
        default="TWITCH_MAIN_TOKEN",
        help="Env key to store extra account token when using --extra-username",
    )
    ap.add_argument("--only-extra", action="store_true", help="Generate token only for --extra-username")
    args = ap.parse_args()

    client_id = TWITCH_CLIENT_ID.strip()
    client_secret = TWITCH_CLIENT_SECRET.strip()
    if not client_id or not client_secret:
        raise RuntimeError("TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET must be set in environment/.env.")

    generated: dict[str, str] = {}

    if not args.only_extra:
        for bot in BOTS:
            name = bot["name"]
            username = bot["username"]
            token = get_token_for_account(
                client_id=client_id,
                client_secret=client_secret,
                username=username,
                redirect_uri=args.redirect_uri,
                timeout_s=args.timeout,
            )
            generated[name] = token
            print(f"Token generated for {name} ({username}).")

    extra_username = args.extra_username.strip()
    if extra_username:
        token = get_token_for_account(
            client_id=client_id,
            client_secret=client_secret,
            username=extra_username,
            redirect_uri=args.redirect_uri,
            timeout_s=args.timeout,
        )
        generated[args.extra_env_key.strip()] = token
        print(f"Token generated for extra account ({extra_username}).")

    print("\nGenerated token env keys:")
    for name, token in generated.items():
        if name.startswith("TWITCH_"):
            key = name
        else:
            key = f"TWITCH_BOT_TOKEN_{name.upper()}"
        print(f"{key}=<generated>")

    if args.write_env:
        env_path = Path(args.env_path)
        for name, token in generated.items():
            if name.startswith("TWITCH_"):
                key = name
            else:
                key = f"TWITCH_BOT_TOKEN_{name.upper()}"
            set_env_value(env_path, key, token)
        print(f"\nUpdated env file: {env_path.resolve()}")
    else:
        print("\nTokens were not displayed. Re-run with --write-env to persist them safely.")


if __name__ == "__main__":
    main()
