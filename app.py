from __future__ import annotations

import logging
import random
import string
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.templating import Jinja2Templates
from starlette.requests import Request

from starlette.middleware.base import BaseHTTPMiddleware

from connection_manager import ConnectionManager
from game import GameManager
from models import GamePhase, GameRoom, Player

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(NoCacheStaticMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

rooms: dict[str, GameRoom] = {}
cm = ConnectionManager()
gm = GameManager(rooms, cm)


def generate_room_code() -> str:
    while True:
        code = "".join(random.choices(string.ascii_uppercase, k=4))
        if code not in rooms:
            return code


class CreateRequest(BaseModel):
    name: str


class CreateResponse(BaseModel):
    room_code: str
    player_id: str


class JoinRequest(BaseModel):
    name: str
    room_code: str


class JoinResponse(BaseModel):
    player_id: str
    room_code: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/create", response_model=CreateResponse)
async def create_room(req: CreateRequest):
    name = req.name.strip()
    if not name:
        return {"error": "Name required"}, 400

    code = generate_room_code()
    player_id = uuid.uuid4().hex[:12]
    player = Player(id=player_id, name=name, is_host=True)

    room = GameRoom(code=code, players={player_id: player})
    rooms[code] = room

    logger.info("Room %s created by %s (%s)", code, name, player_id)
    return CreateResponse(room_code=code, player_id=player_id)


@app.post("/api/join", response_model=JoinResponse)
async def join_room(req: JoinRequest):
    name = req.name.strip()
    code = req.room_code.strip().upper()

    if not name:
        return {"error": "Name required"}, 400

    room = rooms.get(code)
    if not room:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Room not found"})

    if room.phase != GamePhase.LOBBY:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "Game already in progress"})

    # Check for duplicate names
    for p in room.players.values():
        if p.name.lower() == name.lower():
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=400, content={"error": "Name already taken"})

    player_id = uuid.uuid4().hex[:12]
    player = Player(id=player_id, name=name)
    room.players[player_id] = player

    logger.info("Player %s (%s) joined room %s", name, player_id, code)
    return JoinResponse(player_id=player_id, room_code=code)


class RejoinRequest(BaseModel):
    name: str
    room_code: str


@app.post("/api/rejoin", response_model=JoinResponse)
async def rejoin_room(req: RejoinRequest):
    name = req.name.strip()
    code = req.room_code.strip().upper()

    if not name:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"error": "Name required"})

    room = rooms.get(code)
    if not room:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"error": "Room not found"})

    # Find existing player by name (case-insensitive)
    for pid, p in room.players.items():
        if p.name.lower() == name.lower():
            logger.info("Player %s (%s) rejoining room %s", name, pid, code)
            return JoinResponse(player_id=pid, room_code=code)

    # If in lobby, allow joining as new player
    if room.phase == GamePhase.LOBBY:
        player_id = uuid.uuid4().hex[:12]
        player = Player(id=player_id, name=name)
        room.players[player_id] = player
        logger.info("Player %s (%s) joined room %s via rejoin", name, player_id, code)
        return JoinResponse(player_id=player_id, room_code=code)

    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400, content={"error": "Game in progress. Enter your exact name to rejoin."})


@app.websocket("/ws/{room_code}/{player_id}")
async def websocket_endpoint(ws: WebSocket, room_code: str, player_id: str):
    room = rooms.get(room_code)
    if not room or player_id not in room.players:
        await ws.close(code=4001, reason="Invalid room or player")
        return

    await ws.accept()
    cm.add(room_code, player_id, ws)
    room.players[player_id].connected = True
    logger.info("WS connected: %s in room %s", player_id, room_code)

    # Send player list to everyone
    await broadcast_player_list(room_code)

    # If game is in progress, send sync to reconnecting player
    if room.phase != GamePhase.LOBBY:
        await gm.send_sync(room_code, player_id)

    try:
        while True:
            data = await ws.receive_json()
            await handle_message(room_code, player_id, data)
    except WebSocketDisconnect:
        logger.info("WS disconnected: %s from room %s", player_id, room_code)
    except Exception:
        logger.exception("WS error for %s in room %s", player_id, room_code)
    finally:
        cm.remove(room_code, player_id)
        if player_id in room.players:
            room.players[player_id].connected = False
        await broadcast_player_list(room_code)


async def broadcast_player_list(room_code: str) -> None:
    room = rooms.get(room_code)
    if not room:
        return

    player_list = [
        {"id": pid, "name": p.name, "is_host": p.is_host, "connected": p.connected}
        for pid, p in room.players.items()
    ]
    await cm.broadcast(room_code, {"type": "player_list", "players": player_list})


async def handle_message(room_code: str, player_id: str, data: dict) -> None:
    msg_type = data.get("type", "")
    room = rooms.get(room_code)
    if not room:
        return

    if msg_type == "start_game":
        if room.players[player_id].is_host:
            await gm.start_game(room_code)
        else:
            await cm.send_to_player(room_code, player_id, {
                "type": "error",
                "message": "Only the host can start the game",
            })

    elif msg_type == "submit_question":
        question = data.get("question", "")
        await gm.submit_question(room_code, player_id, question)

    elif msg_type == "submit_answer":
        answer_player_id = data.get("answer_player_id", "")
        await gm.submit_answer(room_code, player_id, answer_player_id)

    elif msg_type == "reveal_choice":
        drink = data.get("drink", False)
        await gm.reveal_choice(room_code, player_id, drink)

    elif msg_type == "end_game":
        if room.players[player_id].is_host:
            await gm.end_game(room_code)

    elif msg_type == "return_to_lobby":
        if room.phase == GamePhase.GAME_OVER or room.players[player_id].is_host:
            gm._cancel_timer(room_code)
            room.phase = GamePhase.LOBBY
            room.current_turn = None
            room.current_turn_index = 0
            room.turn_order = []
            room.turn_history = []
            room.round_number = 1
            await cm.broadcast(room_code, {
                "type": "phase_change",
                "phase": GamePhase.LOBBY,
            })
            await broadcast_player_list(room_code)

    else:
        await cm.send_to_player(room_code, player_id, {
            "type": "error",
            "message": f"Unknown message type: {msg_type}",
        })
