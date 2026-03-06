# presence_service/main.py
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, WebSocketException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import asyncio
import json
import math
import jwt
from contextlib import asynccontextmanager

import redis.asyncio as aioredis

import logging
logger = logging.getLogger(__name__)

# ─── Distanz-Schwellen (in Metern) ───────────────────────────
NEAR_DIST = 25.0    # instant
# for FAR Dist      # every Second

# ─── Redis ───────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
CHANNEL_NAME = "positions"

redis_client: aioredis.Redis = None


# ─── Datenmodelle ────────────────────────────────────────────
class UserState:
    def __init__(self, user_id: str, websocket: WebSocket, name: str, department: str):
        self.user_id = user_id
        self.websocket = websocket
        self.name = name
        self.department = department
        self.x = 0.0
        self.y = 0.0


class MoveMessage(BaseModel):
    type: str       # "move"
    x:    float
    y:    float


# ─── Connection Manager ───────────────────────────────────────
class PresenceManager:
    def __init__(self):
        # Alle lokalen WebSocket-Verbindungen auf diesem Pod
        # { user_id: UserState }
        self.users: dict[str, UserState] = {}

        # Update-Queues für verzögertes Broadcasting
        # { empfaenger_user_id: { sender_user_id: position_dict } }
        self.queue_1s:  dict[str, dict] = {}
        self.queue_10s: dict[str, dict] = {}

    # ── Verbindung ────────────────────────────────────────────
    async def connect(self, websocket: WebSocket, user_id: str, name: str, department: str, already_accepted: bool = False):
        if not already_accepted:
            await websocket.accept()

        state = UserState(user_id, websocket, name, department)
        self.users[user_id] = state
        self.queue_1s[user_id]  = {}
        self.queue_10s[user_id] = {}

        print(f"[+] {user_id} ({name}) connected — {len(self.users)} users online")

        # Neuen User allen anderen mitteilen
        await self._broadcast_except(user_id, {
            "type":       "user_joined",
            "user_id":    user_id,
            "name":       name,
            "department": department,
            "x":          state.x,
            "y":          state.y,
        })

        # Dem neuen User alle aktuellen Positionen schicken
        snapshot = [
            {
                "type":       "user_joined",
                "user_id":    uid,
                "name":       u.name,
                "department": u.department,
                "x":          u.x,
                "y":          u.y,
            }
            for uid, u in self.users.items()
            if uid != user_id
        ]
        if snapshot:
            await websocket.send_json({"type": "snapshot", "users": snapshot})

    def disconnect(self, user_id: str):
        self.users.pop(user_id, None)
        self.queue_1s.pop(user_id, None)
        self.queue_10s.pop(user_id, None)
        print(f"[-] {user_id} disconnected — {len(self.users)} users online")

    # ── Bewegung verarbeiten ──────────────────────────────────
    async def handle_move(self, user_id: str, x: float, y: float):
        if user_id not in self.users:
            return

        mover    = self.users[user_id]
        mover.x  = x
        mover.y  = y

        update = {
            "type":    "user_moved",
            "user_id": user_id,
            "x":       x,
            "y":       y,
        }

        # 1. Position in Redis persistieren (für neue Pods / Reconnects)
        await redis_client.set(
            f"pos:{user_id}",
            json.dumps({"x": x, "y": y, "name": mover.name, "department": mover.department}),
            ex=3600  # TTL: 1 Stunde
        )

        # 2. Über Redis Pub/Sub an alle anderen Pods publishen
        await redis_client.publish(CHANNEL_NAME, json.dumps(update))

        # 3. Lokale User nach Distanz einteilen und updaten
        await self._distribute_by_distance(user_id, update)

    async def _distribute_by_distance(self, mover_id: str, update: dict):
        mover = self.users.get(mover_id)
        if not mover:
            return

        for uid, other in self.users.items():
            if uid == mover_id:
                continue

            dist = math.hypot(mover.x - other.x, mover.y - other.y)

            if dist <= NEAR_DIST:
                # Sofort senden
                await self._send(uid, update)

            else:
                # In 10s-Queue
                self.queue_1s[uid][mover_id] = update

    # ── Eingehende Redis-Nachricht (von anderem Pod) ──────────
    async def handle_redis_message(self, data: dict):
        user_id = data.get("user_id")

        # Nur weiterleiten wenn der User NICHT auf diesem Pod ist
        # (sonst haben wir das schon lokal verarbeitet)
        if user_id in self.users:
            return

        # Update an alle lokalen User verteilen
        # (Distanzberechnung nicht möglich da mover nicht lokal ist
        #  → einfach an alle schicken, Client filtert ggf.)
        await self._broadcast_except(user_id, data)

    # ── Queue Flusher ─────────────────────────────────────────
    async def flush_1s(self):
        for user_id, updates in list(self.queue_1s.items()):
            if not updates:
                continue
            for update in updates.values():
                await self._send(user_id, update)
            self.queue_1s[user_id].clear()

    async def flush_10s(self):
        for user_id, updates in list(self.queue_10s.items()):
            if not updates:
                continue
            for update in updates.values():
                await self._send(user_id, update)
            self.queue_10s[user_id].clear()

    # ── Hilfsmethoden ─────────────────────────────────────────
    async def _send(self, user_id: str, data: dict):
        user = self.users.get(user_id)
        if not user:
            return
        try:
            await user.websocket.send_json(data)
        except Exception:
            pass  # Verbindung bereits getrennt

    async def _broadcast_except(self, exclude_id: str, data: dict):
        for uid in list(self.users.keys()):
            if uid != exclude_id:
                await self._send(uid, data)


manager = PresenceManager()


# ─── Background Tasks ─────────────────────────────────────────
async def flush_loop_1s():
    while True:
        await asyncio.sleep(1)
        await manager.flush_1s()


async def flush_loop_10s():
    while True:
        await asyncio.sleep(10)
        await manager.flush_10s()


async def redis_subscriber():
    """Lauscht auf den Redis Pub/Sub Channel und leitet an lokale User weiter."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(CHANNEL_NAME)
    print(f"[Redis] Subscribed to channel '{CHANNEL_NAME}'")

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            data = json.loads(message["data"])
            await manager.handle_redis_message(data)
        except Exception as e:
            print(f"[Redis] Error: {e}")


# ─── Lifespan ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_client = await aioredis.from_url(REDIS_URL, decode_responses=True)
    print("[Redis] Connected")

    # Background Tasks starten
    asyncio.create_task(flush_loop_1s())
    asyncio.create_task(flush_loop_10s())
    asyncio.create_task(redis_subscriber())

    yield

    await redis_client.aclose()
    print("[Redis] Disconnected")


# ─── App ─────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5500",  # Live Server Extension
        "http://127.0.0.1:5500",
        "http://localhost:3000",  # Falls SvelteKit
        "null",  # file:// öffnet mit Origin "null"
        "*", # Development
    ],      # Für Dev alles erlauben
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PUBLIC_KEY = None
KEY_PATH = os.getenv("AUTH_PUBLIC_KEY_PATH", "/secrets/auth_public_key.pem")

try:
    PUBLIC_KEY = open(KEY_PATH).read()
    logger.info("Auth public key loaded – signature verification active")
except FileNotFoundError:
    logger.warning(
        "⚠️  Auth public key NOT found at %s – "
        "running in DEV MODE, JWT signatures are NOT verified!", KEY_PATH
    )


def decode_token(token: str) -> dict:
    if PUBLIC_KEY is None:
        # DEV MODE: Token nur dekodieren, Signatur ignorieren
        logger.warning("DEV MODE: skipping signature check for token", token)
        return jwt.decode(token, options={"verify_signature": False})
    else:
        return jwt.decode(token, PUBLIC_KEY, algorithms=["RS256"])


# ─── WebSocket Endpoint ───────────────────────────────────────
@app.websocket("/ws")
async def presence_ws(websocket: WebSocket, token: str = None):
    if not token:
        # Gast: erst accepten, Namen abwarten
        await websocket.accept()
        user_id = "u_guest_" + str(id(websocket))
        name = "Gast"
        department = ""
        roles = ["guest"]
        try:
            first_msg = await asyncio.wait_for(websocket.receive_json(), timeout=30.0)
            name = first_msg.get("name", "Gast").strip()[:50] \
                if first_msg.get("type") == "set_name" else "Gast"
        except asyncio.TimeoutError:
            name = "Gast"

            # manager.connect darf nicht nochmal accept() aufrufen!
        await manager.connect(websocket, user_id, name, department, True)
    else:
        # ── 1. Token prüfen – nur einmal beim Connect ──────────
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Token expired")
        except jwt.InvalidTokenError:
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")

        # ── 2. User-Daten aus Token extrahieren ─────────────────
        user_id = payload["user_id"]
        name = payload["name"]
        department = payload["department"]
        roles = payload["roles"]
        await manager.connect(websocket, user_id, name, department)

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "move":
                # JWT würde hier den user_id validieren
                await manager.handle_move(
                    user_id=user_id,
                    x=float(data["x"]),
                    y=float(data["y"]),
                )
            elif data.get("type") == "refresh_token":
                # Neuen Token prüfen
                try:
                    new_payload = jwt.decode(data["token"], PUBLIC_KEY, algorithms=["RS256"])
                    # Sicherheitscheck: user_id darf sich nicht ändern!
                    if new_payload["user_id"] != user_id:
                        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Token user_id mismatch")

                    payload = new_payload
                    await websocket.send_json({"type": "token_accepted"})

                except jwt.InvalidTokenError:
                    raise WebSocketException(
                        code=status.WS_1008_POLICY_VIOLATION,
                        reason="Invalid refresh token"
                    )

    except WebSocketDisconnect:
        manager.disconnect(user_id)

        # Allen anderen mitteilen
        await manager._broadcast_except(user_id, {
            "type":    "user_left",
            "user_id": user_id,
        })

        # Redis-Eintrag löschen
        await redis_client.delete(f"pos:{user_id}")


# ─── Health Check ─────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "users_online": len(manager.users)}
