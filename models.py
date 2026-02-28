from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field


class GamePhase(str, enum.Enum):
    LOBBY = "lobby"
    WRITING_QUESTION = "writing_question"
    ANSWERING = "answering"
    REVEAL_CHOICE = "reveal_choice"
    SHOWING_RESULT = "showing_result"
    GAME_OVER = "game_over"


@dataclass
class Player:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    is_host: bool = False
    connected: bool = False


@dataclass
class Turn:
    asker_id: str = ""
    answerer_id: str = ""
    question: str = ""
    answer_player_id: str = ""  # who the answerer named
    revealed: bool = False  # did the named person choose to drink/reveal


@dataclass
class GameRoom:
    code: str = ""
    players: dict[str, Player] = field(default_factory=dict)  # player_id -> Player
    phase: GamePhase = GamePhase.LOBBY
    turn_order: list[str] = field(default_factory=list)  # list of player_ids
    current_turn_index: int = 0
    current_turn: Turn | None = None
    turn_history: list[Turn] = field(default_factory=list)
    round_number: int = 1

    def get_asker_id(self) -> str:
        return self.turn_order[self.current_turn_index % len(self.turn_order)]

    def get_answerer_id(self) -> str:
        return self.turn_order[(self.current_turn_index + 1) % len(self.turn_order)]

    def connected_player_ids(self) -> list[str]:
        return [pid for pid, p in self.players.items() if p.connected]
