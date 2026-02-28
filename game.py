from __future__ import annotations

import asyncio
import logging
import random

from connection_manager import ConnectionManager
from models import GamePhase, GameRoom, Turn
from prompts import get_random_prompts

logger = logging.getLogger(__name__)


class GameManager:
    def __init__(self, rooms: dict[str, GameRoom], cm: ConnectionManager) -> None:
        self.rooms = rooms
        self.cm = cm
        self._timers: dict[str, asyncio.Task] = {}  # room_code -> timer task

    def _cancel_timer(self, room_code: str) -> None:
        task = self._timers.pop(room_code, None)
        if task and not task.done():
            task.cancel()

    def _start_timer(self, room_code: str, seconds: float, coro) -> None:
        self._cancel_timer(room_code)

        async def _wrapper():
            try:
                await asyncio.sleep(seconds)
                await coro
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Timer error in room %s", room_code)

        self._timers[room_code] = asyncio.create_task(_wrapper())

    async def start_game(self, room_code: str) -> None:
        room = self.rooms.get(room_code)
        if not room:
            return

        player_ids = list(room.players.keys())
        if len(player_ids) < 3:
            await self.cm.broadcast(room_code, {
                "type": "error",
                "message": "Need at least 3 players to start",
            })
            return

        random.shuffle(player_ids)
        room.turn_order = player_ids
        room.current_turn_index = 0
        room.round_number = 1
        room.turn_history = []
        await self._start_writing_phase(room_code)

    async def _start_writing_phase(self, room_code: str) -> None:
        room = self.rooms.get(room_code)
        if not room:
            return

        room.phase = GamePhase.WRITING_QUESTION
        asker_id = room.get_asker_id()
        answerer_id = room.get_answerer_id()
        room.current_turn = Turn(asker_id=asker_id, answerer_id=answerer_id)

        asker_name = room.players[asker_id].name
        answerer_name = room.players[answerer_id].name

        await self.cm.broadcast(room_code, {
            "type": "phase_change",
            "phase": GamePhase.WRITING_QUESTION,
            "asker_id": asker_id,
            "asker_name": asker_name,
            "answerer_id": answerer_id,
            "answerer_name": answerer_name,
            "round": room.round_number,
            "turn": room.current_turn_index + 1,
            "total_turns": len(room.turn_order),
            "timeout": 60,
        })

        suggestions = get_random_prompts(3)
        await self.cm.send_to_player(room_code, asker_id, {
            "type": "prompt_question",
            "answerer_name": answerer_name,
            "suggestions": suggestions,
            "timeout": 60,
        })

        self._start_timer(room_code, 60, self._on_writing_timeout(room_code))

    async def _on_writing_timeout(self, room_code: str) -> None:
        room = self.rooms.get(room_code)
        if not room or room.phase != GamePhase.WRITING_QUESTION:
            return
        # Skip this turn
        await self.cm.broadcast(room_code, {
            "type": "turn_result",
            "skipped": True,
            "reason": "Asker ran out of time",
            "asker_name": room.players[room.current_turn.asker_id].name,
        })
        await self._advance_turn(room_code)

    async def submit_question(self, room_code: str, player_id: str, question: str) -> None:
        room = self.rooms.get(room_code)
        if not room or room.phase != GamePhase.WRITING_QUESTION:
            return
        if not room.current_turn or room.current_turn.asker_id != player_id:
            return

        self._cancel_timer(room_code)
        question = question.strip()
        if not question:
            return

        room.current_turn.question = question
        room.phase = GamePhase.ANSWERING
        answerer_id = room.current_turn.answerer_id

        # Send player list for answerer to pick from (exclude the answerer)
        other_players = [
            {"id": pid, "name": p.name}
            for pid, p in room.players.items()
            if pid != answerer_id
        ]

        await self.cm.broadcast(room_code, {
            "type": "phase_change",
            "phase": GamePhase.ANSWERING,
            "answerer_id": answerer_id,
            "answerer_name": room.players[answerer_id].name,
            "timeout": 30,
        })

        await self.cm.send_to_player(room_code, answerer_id, {
            "type": "prompt_answer",
            "question": question,
            "players": other_players,
            "timeout": 30,
        })

        self._start_timer(room_code, 30, self._on_answering_timeout(room_code))

    async def _on_answering_timeout(self, room_code: str) -> None:
        room = self.rooms.get(room_code)
        if not room or room.phase != GamePhase.ANSWERING:
            return
        # Pick random answer
        answerer_id = room.current_turn.answerer_id
        candidates = [pid for pid in room.players if pid != answerer_id]
        random_pick = random.choice(candidates)
        await self.submit_answer(room_code, answerer_id, random_pick, timed_out=True)

    async def submit_answer(self, room_code: str, player_id: str, answer_player_id: str, timed_out: bool = False) -> None:
        room = self.rooms.get(room_code)
        if not room or room.phase != GamePhase.ANSWERING:
            return
        if not room.current_turn or room.current_turn.answerer_id != player_id:
            return
        if answer_player_id not in room.players:
            return

        self._cancel_timer(room_code)
        room.current_turn.answer_player_id = answer_player_id
        room.phase = GamePhase.REVEAL_CHOICE

        named_name = room.players[answer_player_id].name
        answerer_name = room.players[player_id].name

        await self.cm.broadcast(room_code, {
            "type": "answer_given",
            "answerer_name": answerer_name,
            "named_player_id": answer_player_id,
            "named_player_name": named_name,
            "timed_out": timed_out,
        })

        await self.cm.broadcast(room_code, {
            "type": "phase_change",
            "phase": GamePhase.REVEAL_CHOICE,
            "named_player_id": answer_player_id,
            "named_player_name": named_name,
            "timeout": 15,
        })

        await self.cm.send_to_player(room_code, answer_player_id, {
            "type": "prompt_reveal",
            "message": f"{answerer_name} answered your name! Drink to reveal the question, or pass.",
            "timeout": 15,
        })

        self._start_timer(room_code, 15, self._on_reveal_timeout(room_code))

    async def _on_reveal_timeout(self, room_code: str) -> None:
        room = self.rooms.get(room_code)
        if not room or room.phase != GamePhase.REVEAL_CHOICE:
            return
        await self.reveal_choice(room_code, room.current_turn.answer_player_id, drink=False, timed_out=True)

    async def reveal_choice(self, room_code: str, player_id: str, drink: bool, timed_out: bool = False) -> None:
        room = self.rooms.get(room_code)
        if not room or room.phase != GamePhase.REVEAL_CHOICE:
            return
        if not room.current_turn or room.current_turn.answer_player_id != player_id:
            return

        self._cancel_timer(room_code)
        room.current_turn.revealed = drink
        room.phase = GamePhase.SHOWING_RESULT

        turn = room.current_turn
        result = {
            "type": "turn_result",
            "skipped": False,
            "asker_name": room.players[turn.asker_id].name,
            "answerer_name": room.players[turn.answerer_id].name,
            "named_player_name": room.players[turn.answer_player_id].name,
            "drink": drink,
            "timed_out": timed_out,
        }
        if drink:
            result["question"] = turn.question

        await self.cm.broadcast(room_code, result)

        room.turn_history.append(turn)

        # Auto-advance after 4 seconds
        self._start_timer(room_code, 4, self._advance_turn(room_code))

    async def _advance_turn(self, room_code: str) -> None:
        room = self.rooms.get(room_code)
        if not room:
            return

        room.current_turn_index += 1

        if room.current_turn_index >= len(room.turn_order):
            # Round complete - start new round
            room.current_turn_index = 0
            room.round_number += 1
            random.shuffle(room.turn_order)

        await self._start_writing_phase(room_code)

    async def end_game(self, room_code: str) -> None:
        room = self.rooms.get(room_code)
        if not room:
            return

        self._cancel_timer(room_code)
        room.phase = GamePhase.GAME_OVER

        recap = []
        for turn in room.turn_history:
            entry = {
                "asker_name": room.players.get(turn.asker_id, None) and room.players[turn.asker_id].name,
                "answerer_name": room.players.get(turn.answerer_id, None) and room.players[turn.answerer_id].name,
                "named_player_name": room.players.get(turn.answer_player_id, None) and room.players[turn.answer_player_id].name,
                "revealed": turn.revealed,
            }
            if turn.revealed:
                entry["question"] = turn.question
            recap.append(entry)

        await self.cm.broadcast(room_code, {
            "type": "phase_change",
            "phase": GamePhase.GAME_OVER,
            "recap": recap,
        })

    async def send_sync(self, room_code: str, player_id: str) -> None:
        """Send full game state to a reconnecting player."""
        room = self.rooms.get(room_code)
        if not room:
            return

        player_list = [
            {"id": pid, "name": p.name, "is_host": p.is_host, "connected": p.connected}
            for pid, p in room.players.items()
        ]

        sync_msg: dict = {
            "type": "sync",
            "phase": room.phase,
            "player_id": player_id,
            "is_host": room.players[player_id].is_host,
            "room_code": room_code,
            "players": player_list,
        }

        if room.phase != GamePhase.LOBBY and room.phase != GamePhase.GAME_OVER and room.current_turn:
            turn = room.current_turn
            sync_msg["turn_info"] = {
                "asker_id": turn.asker_id,
                "asker_name": room.players[turn.asker_id].name,
                "answerer_id": turn.answerer_id,
                "answerer_name": room.players[turn.answerer_id].name,
                "round": room.round_number,
                "turn": room.current_turn_index + 1,
                "total_turns": len(room.turn_order),
            }

            if room.phase == GamePhase.ANSWERING and player_id == turn.answerer_id:
                other_players = [
                    {"id": pid, "name": p.name}
                    for pid, p in room.players.items()
                    if pid != turn.answerer_id
                ]
                sync_msg["prompt_answer"] = {
                    "question": turn.question,
                    "players": other_players,
                }

            if room.phase == GamePhase.REVEAL_CHOICE:
                sync_msg["turn_info"]["named_player_id"] = turn.answer_player_id
                sync_msg["turn_info"]["named_player_name"] = room.players[turn.answer_player_id].name

            if room.phase == GamePhase.WRITING_QUESTION and player_id == turn.asker_id:
                sync_msg["prompt_question"] = {
                    "answerer_name": room.players[turn.answerer_id].name,
                    "suggestions": get_random_prompts(3),
                }

        if room.phase == GamePhase.GAME_OVER:
            recap = []
            for t in room.turn_history:
                entry = {
                    "asker_name": room.players.get(t.asker_id, None) and room.players[t.asker_id].name,
                    "answerer_name": room.players.get(t.answerer_id, None) and room.players[t.answerer_id].name,
                    "named_player_name": room.players.get(t.answer_player_id, None) and room.players[t.answer_player_id].name,
                    "revealed": t.revealed,
                }
                if t.revealed:
                    entry["question"] = t.question
                recap.append(entry)
            sync_msg["recap"] = recap

        await self.cm.send_to_player(room_code, player_id, sync_msg)
