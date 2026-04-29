import importlib


TOKEN_ENV = {
    "TWITCH_BOT_TOKEN_SIENNA": "oauth:sienna_token",
    "TWITCH_BOT_TOKEN_KNIGHT": "oauth:knight_token",
    "TWITCH_BOT_TOKEN_SIMP": "oauth:simp_token",
}


def _reload_config(monkeypatch, values):
    keys = {
        "OPENAI_API_KEY",
        "TWITCH_CLIENT_ID",
        "TWITCH_CLIENT_SECRET",
        "BOTS_DISABLE_DOTENV",
        "BOTS_DOTENV_PATH",
        "BOTS_DOTENV_OVERRIDE",
        *TOKEN_ENV.keys(),
    }
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BOTS_DISABLE_DOTENV", "1")
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    import config

    return importlib.reload(config)


def _valid_env():
    return {
        "OPENAI_API_KEY": "openai_key",
        "TWITCH_CLIENT_ID": "client_id",
        "TWITCH_CLIENT_SECRET": "client_secret",
        **TOKEN_ENV,
    }


def test_validate_runtime(monkeypatch):
    config = _reload_config(monkeypatch, _valid_env())
    assert config.validate_runtime(require_helix=True) == []


def test_validate_runtime_missing_openai(monkeypatch):
    env = _valid_env()
    env.pop("OPENAI_API_KEY")
    config = _reload_config(monkeypatch, env)
    assert "Missing OPENAI_API_KEY" in config.validate_runtime(require_helix=True)


def test_validate_runtime_missing_twitch_bot_token(monkeypatch):
    env = _valid_env()
    env.pop("TWITCH_BOT_TOKEN_SIMP")
    config = _reload_config(monkeypatch, env)
    assert "Missing token for bot 'simp'" in config.validate_runtime(require_helix=True)


def test_validate_runtime_missing_helix_credentials(monkeypatch):
    env = _valid_env()
    env.pop("TWITCH_CLIENT_ID")
    env.pop("TWITCH_CLIENT_SECRET")
    config = _reload_config(monkeypatch, env)
    errors = config.validate_runtime(require_helix=True)
    assert "Missing TWITCH_CLIENT_ID" in errors
    assert "Missing TWITCH_CLIENT_SECRET" in errors
    assert "Missing TWITCH_CLIENT_ID" not in config.validate_runtime(require_helix=False)


def test_validate_runtime_malformed_twitch_token(monkeypatch):
    env = _valid_env()
    env["TWITCH_BOT_TOKEN_KNIGHT"] = "missing_prefix"
    config = _reload_config(monkeypatch, env)
    assert "Malformed token for bot 'knight' (expected oauth: prefix)" in config.validate_runtime(require_helix=True)


def test_dotenv_does_not_override_existing_env(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=from_file\n", encoding="utf-8")

    env = _valid_env()
    env["OPENAI_API_KEY"] = "from_env"
    env["BOTS_DOTENV_PATH"] = str(env_file)
    env["BOTS_DISABLE_DOTENV"] = "0"
    config = _reload_config(monkeypatch, env)

    assert config.OPENAI_API_KEY == "from_env"
