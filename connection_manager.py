from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class ConnectionManager:
    # room_code -> {player_id -> WebSocket}
    _connections: dict[str, dict[str, WebSocket]] = field(default_factory=dict)

    def add(self, room_code: str, player_id: str, ws: WebSocket) -> None:
        if room_code not in self._connections:
            self._connections[room_code] = {}
        self._connections[room_code][player_id] = ws

    def remove(self, room_code: str, player_id: str) -> None:
        if room_code in self._connections:
            self._connections[room_code].pop(player_id, None)
            if not self._connections[room_code]:
                del self._connections[room_code]

    def get(self, room_code: str, player_id: str) -> WebSocket | None:
        return self._connections.get(room_code, {}).get(player_id)

    async def send_to_player(self, room_code: str, player_id: str, message: dict) -> None:
        ws = self.get(room_code, player_id)
        if ws:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                logger.warning("Failed to send to player %s in room %s", player_id, room_code)

    async def broadcast(self, room_code: str, message: dict, exclude: str | None = None) -> None:
        conns = self._connections.get(room_code, {})
        for player_id, ws in list(conns.items()):
            if player_id == exclude:
                continue
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                logger.warning("Failed to broadcast to player %s in room %s", player_id, room_code)

    def room_player_ids(self, room_code: str) -> list[str]:
        return list(self._connections.get(room_code, {}).keys())
