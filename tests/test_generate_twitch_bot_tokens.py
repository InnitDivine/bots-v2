from http.server import HTTPServer

import pytest

import generate_twitch_bot_tokens as tokens


def test_wait_for_callback_timeout():
    tokens.OAuthCallbackHandler.code = None
    tokens.OAuthCallbackHandler.state = None
    tokens.OAuthCallbackHandler.error = None
    tokens.OAuthCallbackHandler.done.clear()
    server = HTTPServer(("127.0.0.1", 0), tokens.OAuthCallbackHandler)
    try:
        assert tokens.wait_for_callback(server, timeout_s=0) == (None, None, "timeout")
    finally:
        server.server_close()


def test_validate_returned_state_mismatch():
    with pytest.raises(RuntimeError, match="State mismatch"):
        tokens.validate_returned_state("expected", "wrong", "sienna")


def test_exchange_code_for_token_formats_oauth(monkeypatch):
    monkeypatch.setattr(tokens, "_post_json", lambda url, data: {"access_token": "raw_token"})
    assert tokens.exchange_code_for_token("id", "secret", "code", "http://localhost:3000/callback") == "oauth:raw_token"


def test_exchange_code_for_token_preserves_oauth_prefix(monkeypatch):
    monkeypatch.setattr(tokens, "_post_json", lambda url, data: {"access_token": "oauth:raw_token"})
    assert tokens.exchange_code_for_token("id", "secret", "code", "http://localhost:3000/callback") == "oauth:raw_token"
