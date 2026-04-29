import json
import os
import time
from typing import Any

from config import GAMEBANK_JSON, GAMEBANK_TXT


class GameBankManager:
    def __init__(self):
        self.path_json = GAMEBANK_JSON
        self.path_txt = GAMEBANK_TXT
        self.bank: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self):
        if not os.path.exists(self.path_json):
            self.bank = {}
            return
        try:
            with open(self.path_json, "r", encoding="utf-8") as f:
                self.bank = json.load(f)
        except Exception:
            self.bank = {}

    def _save(self):
        with open(self.path_json, "w", encoding="utf-8") as f:
            json.dump(self.bank, f, indent=2, ensure_ascii=False)

    def _append_txt(self, game: str, data: dict[str, Any]):
        with open(self.path_txt, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 60 + "\n")
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] GAME: {game}\n")
            for k, v in data.items():
                f.write(f"- {k}: {v}\n")

    def _auto_bank_for_game(self, game: str) -> dict[str, Any]:
        g = game.lower()
        topics = [
            "progress and current objective",
            "build/loadout choices",
            "hard fights or difficult sections",
            "favorite moments so far",
        ]
        questions = [
            "whats the plan for this run",
            "favorite part of this game so far",
            "any risky strat coming up",
            "how close are you to pb pace",
        ]
        ban_terms = ["giveaway", "hack", "cheat engine"]

        if "souls" in g or "elden" in g:
            topics.append("boss route and rune management")
            questions.append("which boss is next")
        if "valorant" in g or "cs" in g:
            topics.append("economy and utility timing")
            questions.append("full buy next round")

        return {
            "topics": topics,
            "questions": questions,
            "ban_terms": ban_terms,
        }

    def ensure(self, game: str):
        game = (game or "").strip()
        if not game:
            return
        if game in self.bank:
            return
        generated = self._auto_bank_for_game(game)
        self.bank[game] = generated
        self._save()
        self._append_txt(game, generated)

    def get(self, game: str) -> dict[str, Any] | None:
        return self.bank.get((game or "").strip())
