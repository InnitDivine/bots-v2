import ast
import json

import pytest

from add_bot_assistant import (
    update_bot_persona_py,
    update_config_py,
    update_env_file,
    validate_bot_username,
    validate_persona_json,
)


def test_validate_bot_username():
    assert validate_bot_username("Spark_Bot") == "spark_bot"
    with pytest.raises(ValueError):
        validate_bot_username("bad name")
    with pytest.raises(ValueError):
        validate_bot_username("abc")


def test_validate_persona_json():
    persona = validate_persona_json('{"tone":"chill","phrasing":"short lines","llm_temp":0.8}')
    assert persona == {"tone": "chill", "phrasing": "short lines", "llm_temp": 0.8}
    with pytest.raises(ValueError):
        validate_persona_json("{broken")
    with pytest.raises(ValueError):
        validate_persona_json('{"tone":"x","phrasing":"y","llm_temp":1.5}')


def test_update_env_file_normalizes_oauth_prefix(tmp_path):
    env = tmp_path / ".env"
    env.write_text("EXISTING=1\nTWITCH_BOT_TOKEN_SPARKY=old\n", encoding="utf-8")

    update_env_file("sparky", "raw_token", env)

    text = env.read_text(encoding="utf-8")
    assert "EXISTING=1" in text
    assert "TWITCH_BOT_USERNAME_SPARKY=sparky" in text
    assert "TWITCH_BOT_TOKEN_SPARKY=oauth:raw_token" in text
    assert "old" not in text


def test_update_config_py_uses_ast_assignment_bounds(tmp_path):
    config = tmp_path / "config.py"
    config.write_text(
        "\n".join(
            [
                "def _env(name, default=''):",
                "    return default",
                "BOTS = [",
                "    {'name': 'sienna'},",
                "]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    update_config_py("sparky", True, (10, 20), config)
    ast.parse(config.read_text(encoding="utf-8"))
    ns = {}
    exec(config.read_text(encoding="utf-8"), ns)

    assert ns["BOTS"][-1]["name"] == "sparky"
    assert ns["BOTS"][-1]["is_moderator"] is True
    assert ns["BOTS"][-1]["message_frequency"] == (10, 20)


def test_update_bot_persona_py_uses_ast_assignment_bounds(tmp_path):
    persona_file = tmp_path / "bot_persona.py"
    persona_file.write_text(
        "PERSONA_STYLE = {\n    'sienna': {'tone': 'x', 'phrasing': 'y', 'llm_temp': 0.7},\n}\n",
        encoding="utf-8",
    )

    update_bot_persona_py("sparky", {"tone": "calm", "phrasing": "tiny lines", "llm_temp": 0.75}, persona_file)
    ast.parse(persona_file.read_text(encoding="utf-8"))
    ns = {}
    exec(persona_file.read_text(encoding="utf-8"), ns)

    assert ns["PERSONA_STYLE"]["sparky"] == {
        "tone": "calm",
        "phrasing": "tiny lines",
        "llm_temp": 0.75,
    }
