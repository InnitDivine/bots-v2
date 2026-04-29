import json

from gamebank import GameBankManager


def test_gamebank_ensure(tmp_path, monkeypatch):
    j = tmp_path / "gamebank.json"
    t = tmp_path / "gamebank.txt"

    monkeypatch.setenv("GAMEBANK_JSON", str(j))
    monkeypatch.setenv("GAMEBANK_TXT", str(t))

    import importlib
    import config
    import gamebank

    importlib.reload(config)
    importlib.reload(gamebank)

    gb = gamebank.GameBankManager()
    gb.ensure("Elden Ring")

    data = json.loads(j.read_text(encoding="utf-8"))
    assert "Elden Ring" in data
    assert data["Elden Ring"]["questions"]
