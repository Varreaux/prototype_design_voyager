"""
play_session_hvh.py
===================
Human-vs-human card game sessions for the dashboard's Play tab.

Two phones (Julian = P1, Tim = P2) play each other while the laptop shows the
live board on the projector. Each session is keyed by a uuid so multiple games
could in principle run side-by-side, though the dashboard UI only ever exposes
one at a time.

State lives entirely server-side; clients (phones + spectator) just render
snapshots pushed over WebSockets. Reconnects are handled by replaying the
latest snapshot to whoever just attached.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from typing import Any, Dict, List, Optional, Set

from card_game import CardGame, _fresh_state, PLAYER_1, PLAYER_2
from web.aivai_match import (
    DEFAULT_LOADOUT_N,
    _serialize_state,
    load_mechanics_by_name,
    load_top_mechanics,
)
from web.play_session import _apply_one_move, _predict_hand_triggers


# Map between the user-visible role names and the underlying player numbers.
ROLE_JULIAN = "julian"
ROLE_TIM    = "tim"
ROLE_SPEC   = "spectator"
ROLE_TO_PLAYER = {ROLE_JULIAN: PLAYER_1, ROLE_TIM: PLAYER_2}


# ── Session storage ─────────────────────────────────────────────────────────

# Per-process registry. Keyed by session_id (uuid hex). The lock guards
# mutation of `_SESSIONS` itself; per-session state is mutated under each
# session's own lock to keep concurrent move submissions safe.
_REGISTRY_LOCK = threading.Lock()
_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _new_session_dict(game: CardGame, loadout: list) -> Dict[str, Any]:
    return {
        "id":          uuid.uuid4().hex[:12],
        "game":        game,
        "loadout":     loadout,
        "lock":        threading.Lock(),
        # Each connected client (julian, tim, spectator) gets an asyncio.Queue
        # the WebSocket handler awaits. broadcast() puts a snapshot in each.
        "listeners":   set(),  # type: Set[asyncio.Queue]
        # Last broadcast snapshot, replayed to new connections so a late
        # spectator (or a phone that reconnected) sees current state.
        "last_snapshot": None,
        # Track which roles have ever connected so the spectator can show
        # "waiting for Julian / Tim to scan" hints.
        "connected_roles": set(),  # type: Set[str]
    }


def _loadout_summary(loadout: list) -> list:
    return [
        {
            "name":        m["name"],
            "description": m["description"],
            "aggregate":   m["aggregate"],
            "patched":     m.get("patched", False),
        }
        for m in loadout
    ]


# ── Public API ──────────────────────────────────────────────────────────────

def create_session(mechanic_names: Optional[List[str]] = None) -> str:
    """
    Build a fresh HvH session and return its id. The caller (typically the
    HTTP handler) hands the id back to the spectator panel, which uses it to
    construct the two phone QR URLs.
    """
    if mechanic_names:
        loadout = load_mechanics_by_name(mechanic_names)
    else:
        loadout = load_top_mechanics(DEFAULT_LOADOUT_N)
    fns = [m["fn"] for m in loadout]

    game = CardGame(
        state=_fresh_state(),
        # Both players are human; no agents needed.
        ai_players={PLAYER_1: None, PLAYER_2: None},
        mechanics=fns,
    )

    sess = _new_session_dict(game, loadout)
    with _REGISTRY_LOCK:
        _SESSIONS[sess["id"]] = sess
    return sess["id"]


def end_session(session_id: str) -> None:
    with _REGISTRY_LOCK:
        _SESSIONS.pop(session_id, None)


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    with _REGISTRY_LOCK:
        return _SESSIONS.get(session_id)


def snapshot(session_id: str) -> Optional[dict]:
    """Build a JSON-safe snapshot of the session for one of the clients."""
    sess = get_session(session_id)
    if sess is None:
        return None
    game: CardGame = sess["game"]
    state = game.get_state()
    finished = game.game_finished()
    current_player = int(state.get("current_player", PLAYER_1))
    # Predict triggers for whichever player is on the clock so that phone
    # can pulse the cards that would fire a mechanic. Indices align with
    # the current player's hand; phones gate rendering on it being their
    # own turn so the predictions never decorate the wrong hand.
    hand_triggers = ([] if finished
                     else _predict_hand_triggers(game, sess["loadout"], current_player))
    return {
        "type":          "state",
        "session_id":    sess["id"],
        "loadout":       _loadout_summary(sess["loadout"]),
        "state":         _serialize_state(state),
        "legal_moves":   [] if finished else list(game.possible_moves(state)),
        "current_player": current_player,
        "finished":      finished,
        "winner":        game.get_winner(),
        "hand_triggers": hand_triggers,
        "connected": {
            ROLE_JULIAN: ROLE_JULIAN in sess["connected_roles"],
            ROLE_TIM:    ROLE_TIM    in sess["connected_roles"],
        },
    }


def apply_move(session_id: str, role: str, card_index: int) -> dict:
    """
    Validate and apply a move from one of the human players. Returns
    {"ok": True, "events": [...]} on success or {"error": str} on failure.

    Caller is responsible for broadcasting the resulting snapshot to all
    connected clients — apply_move only mutates state.
    """
    sess = get_session(session_id)
    if sess is None:
        return {"error": "Session not found."}
    if role not in ROLE_TO_PLAYER:
        return {"error": f"Invalid role {role!r} (expected julian or tim)."}

    player = ROLE_TO_PLAYER[role]
    game: CardGame = sess["game"]

    with sess["lock"]:
        if game.game_finished():
            return {"error": "Game is already over."}

        state = game.get_state()
        if int(state["current_player"]) != player:
            return {"error": "Not your turn."}

        legal = game.possible_moves(state)
        try:
            idx = int(card_index)
        except (TypeError, ValueError):
            return {"error": f"Invalid card index: {card_index!r}"}
        if idx not in legal:
            return {"error": f"Card {idx} is not a legal move (legal: {legal})."}

        event = _apply_one_move(game, sess["loadout"], idx)
        return {"ok": True, "events": [event]}


# ── Listener plumbing ───────────────────────────────────────────────────────

def attach_listener(session_id: str, queue: asyncio.Queue, role: str) -> bool:
    """Register a queue to receive every subsequent state broadcast."""
    sess = get_session(session_id)
    if sess is None:
        return False
    sess["listeners"].add(queue)
    if role in (ROLE_JULIAN, ROLE_TIM):
        sess["connected_roles"].add(role)
    return True


def detach_listener(session_id: str, queue: asyncio.Queue, role: str) -> None:
    sess = get_session(session_id)
    if sess is None:
        return
    sess["listeners"].discard(queue)
    if role in (ROLE_JULIAN, ROLE_TIM):
        sess["connected_roles"].discard(role)


def broadcast(session_id: str, message: dict) -> None:
    """
    Push a message to every listener queue. Safe to call from the same event
    loop as the listeners (uses put_nowait, never awaits). The WebSocket
    handler reading the queue handles back-pressure / drops.
    """
    sess = get_session(session_id)
    if sess is None:
        return
    if message.get("type") == "state":
        sess["last_snapshot"] = message
    for q in list(sess["listeners"]):
        try:
            q.put_nowait(message)
        except asyncio.QueueFull:
            # Drop the message rather than block. The next state broadcast
            # will catch the client up — state messages are idempotent.
            pass
