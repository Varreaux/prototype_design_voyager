"""
play_session.py
===============
Stateful human-vs-MCTS card game session for the dashboard's
"Play vs AI" panel.

Keeps a single active CardGame in module-level memory (the dashboard is
single-user) and exposes two operations:

  * start_session(simulations, mechanic_names)  — start a fresh game
  * submit_human_move(card_index)               — apply human move, then
                                                   loop AI moves until
                                                   human's turn again
                                                   or game ends

Per-move events use the same shape as web.aivai_match.run_match so the
frontend can reuse its rendering logic (banner, score flashes, card chips).

This module assumes the human is always Player 1 and MCTS is Player 2.
"""

from __future__ import annotations

import copy
import threading
from typing import Any, Dict, List, Optional

from card_game import CardGame, _fresh_state, PLAYER_1, PLAYER_2
from mcts_agent import MCTSAgent, MinimaxAgent
from web.aivai_match import (
    DEFAULT_LOADOUT_N,
    DEFAULT_MCTS_SIMS,
    MAX_TURNS_HARD_CAP,
    _serialize_state,
    _state_changed,
    load_mechanics_by_name,
    load_top_mechanics,
)


# Default minimax depth: depth 8 lands at roughly 1-2s/move on the card game,
# which is the strongest setting that still feels interactive.
DEFAULT_MINIMAX_DEPTH = 8


_LOCK = threading.Lock()
_SESSION: Optional[Dict[str, Any]] = None   # {'game', 'simulations', 'loadout'}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _loadout_summary(loadout: list) -> list:
    """Strip the callable so the loadout is JSON-safe for the frontend."""
    return [
        {
            "name":        m["name"],
            "description": m["description"],
            "aggregate":   m["aggregate"],
            "patched":     m.get("patched", False),
        }
        for m in loadout
    ]


def _apply_one_move(game: CardGame, loadout: list, chosen) -> dict:
    """
    Apply one move to `game`. Records a per-move event with the same shape as
    aivai_match.run_match: before_move, after_raw_move (after card played but
    before mechanics), per-mechanic events, and final after-state.
    """
    state = game.get_state()
    player = int(state["current_player"])
    before_snapshot = _serialize_state(state)

    # Compute the "raw move" snapshot (post-card, pre-mechanic) the same way
    # aivai_match does, so the per-mechanic deltas line up correctly.
    raw_state = copy.deepcopy(state)
    hand = raw_state["hands"][player]
    if chosen == -1 or not hand:
        card_played = None
        raw_state["last_played"] = None
    else:
        card_played = hand.pop(int(chosen))
        raw_state["scores"][player] += card_played
        raw_state["last_played"] = card_played
    after_raw_snapshot = _serialize_state(raw_state)

    # Walk the mechanics one at a time off the raw_state so we can attribute
    # each visible state change to its source mechanic.
    per_mech_events: List[dict] = []
    running = copy.deepcopy(raw_state)
    prev = copy.deepcopy(raw_state)
    for mech in loadout:
        fn = mech["fn"]
        try:
            result = fn(running)
            if isinstance(result, dict):
                running = result
        except Exception as e:
            per_mech_events.append({
                "name":  mech["name"],
                "fired": False,
                "error": f"{type(e).__name__}: {e}",
                "after": _serialize_state(prev),
                "score_changes": {}, "hand_changes": {},
                "extra_turn_changed": False, "custom_state_changed": False,
            })
            continue
        diff = _state_changed(prev, running)
        per_mech_events.append({
            "name":               mech["name"],
            "fired":              diff["fired"],
            "score_changes":      diff["score_changes"],
            "hand_changes":       diff["hand_changes"],
            "extra_turn_changed": diff["extra_turn_changed"],
            "custom_state_changed": diff["custom_state_changed"],
            "after":              _serialize_state(running),
        })
        prev = copy.deepcopy(running)

    # Now actually advance the real game (this re-runs the mechanics atomically
    # via perform_move). Then advance_turn so current_player is set correctly
    # for the next decision.
    error = None
    try:
        game.perform_move(chosen)
        game.advance_turn()
    except Exception as e:
        error = f"perform_move crashed: {type(e).__name__}: {e}"

    event: Dict[str, Any] = {
        "turn":           int(state.get("turn", 0)),
        "player":         player,
        "card_index":     int(chosen) if isinstance(chosen, int) else None,
        "card_played":    card_played,
        "before_move":    before_snapshot,
        "after_raw_move": after_raw_snapshot,
        "mechanics":      per_mech_events,
        "after":          _serialize_state(game.get_state()),
    }
    if error:
        event["error"] = error
    return event


# ── Trigger preview ──────────────────────────────────────────────────────────

def _predict_hand_triggers(game: CardGame, loadout: list,
                           player: int = PLAYER_1) -> list:
    """
    For every card in `player`'s hand, simulate playing it and return the
    list of mechanic names that would fire on that play. Lets the phone /
    dashboard pulse cards in their would-trigger colors so the player can
    see consequences before committing.

    Returns a list aligned with the hand: predictions[i] is a list of
    mechanic names that would fire if `player` plays the card at index i.
    Empty list = playing this card triggers nothing visible.
    """
    state = game.get_state()
    hand = list((state.get('hands') or {}).get(player, []))
    if not hand:
        return []

    predictions: List[List[str]] = []
    for i, card_value in enumerate(hand):
        # Build the post-raw-move state (card popped, score bumped, last_played
        # set), exactly like CardGame.perform_move would, then walk mechanics
        # in loadout order to see which ones produce visible state changes.
        sim_hand = list(hand)
        sim_hand.pop(i)
        sim_state = copy.deepcopy(state)
        sim_state['hands'][player] = sim_hand
        sim_state['scores'][player] = (sim_state['scores'].get(player, 0)
                                       + int(card_value))
        sim_state['last_played'] = int(card_value)
        sim_state['extra_turn']  = False   # advance_turn consumed any prior flag

        running = copy.deepcopy(sim_state)
        prev    = copy.deepcopy(sim_state)
        fired_names: List[str] = []
        for mech in loadout:
            try:
                result = mech['fn'](running)
                if isinstance(result, dict):
                    running = result
            except Exception:
                continue
            diff = _state_changed(prev, running)
            if diff['fired']:
                fired_names.append(mech['name'])
            prev = copy.deepcopy(running)

        predictions.append(fired_names)

    return predictions


# ── Public API ───────────────────────────────────────────────────────────────

def start_session(simulations: int = DEFAULT_MCTS_SIMS,
                  mechanic_names: Optional[List[str]] = None,
                  agent_type: str = "mcts",
                  depth: int = DEFAULT_MINIMAX_DEPTH) -> dict:
    """
    Start a new human-vs-AI session. Player 1 is the human; Player 2 is
    either an MCTS agent (at the requested sim budget) or a minimax agent
    (at the requested search depth).

    Args:
        simulations    : MCTS simulations per move (used when agent_type='mcts')
        mechanic_names : optional override for the default top-2 loadout
        agent_type     : 'mcts' or 'minimax'
        depth          : minimax search depth (used when agent_type='minimax')
    """
    global _SESSION

    agent_type = (agent_type or "mcts").lower()
    if agent_type not in ("mcts", "minimax"):
        agent_type = "mcts"

    sims  = max(1, int(simulations or DEFAULT_MCTS_SIMS))
    depth = max(2, int(depth or DEFAULT_MINIMAX_DEPTH))

    if mechanic_names:
        loadout = load_mechanics_by_name(mechanic_names)
    else:
        loadout = load_top_mechanics(DEFAULT_LOADOUT_N)

    fns = [m["fn"] for m in loadout]

    if agent_type == "minimax":
        # Cap wall-clock per move to 6s so deep settings don't lock the UI.
        p2_agent = MinimaxAgent(max_depth=depth, time_budget_s=6.0)
    else:
        p2_agent = MCTSAgent(simulations=sims)

    game = CardGame(
        state=_fresh_state(),
        ai_players={PLAYER_1: None, PLAYER_2: p2_agent},   # P1 is human
        mechanics=fns,
    )

    with _LOCK:
        _SESSION = {
            "game":        game,
            "simulations": sims,
            "depth":       depth,
            "agent_type":  agent_type,
            "loadout":     loadout,
        }

    initial_state = game.get_state()
    return {
        "simulations":   sims,
        "depth":         depth,
        "agent_type":    agent_type,
        "loadout":       _loadout_summary(loadout),
        "state":         _serialize_state(initial_state),
        "legal_moves":   list(game.possible_moves(initial_state)),
        "finished":      game.game_finished(),
        "winner":        game.get_winner(),
        "hand_triggers": _predict_hand_triggers(game, loadout, PLAYER_1),
    }


def submit_human_move(card_index: int) -> dict:
    """
    Apply the human's move (card_index in their current hand), then run any
    AI turns that follow until either the game ends or it is the human's
    turn again.

    Returns:
        {
          'events':      [move_event, ...],   # human's move + any AI moves
          'state':       serialized final state,
          'legal_moves': legal moves for whoever plays next,
          'finished':    bool,
          'winner':      1 | 2 | None,
        }
        On error: {'error': str}
    """
    with _LOCK:
        sess = _SESSION
    if sess is None:
        return {"error": "No active session. Click New Game to start one."}

    game: CardGame = sess["game"]
    loadout = sess["loadout"]

    state = game.get_state()
    if game.game_finished():
        return {"error": "Game is already over. Click New Game to play again."}

    if int(state["current_player"]) != PLAYER_1:
        return {"error": "Not your turn — the AI is still playing."}

    legal = game.possible_moves(state)
    try:
        idx = int(card_index)
    except (TypeError, ValueError):
        return {"error": f"Invalid card index: {card_index!r}"}
    if idx not in legal:
        return {"error": f"Card index {idx} is not a legal move (legal: {legal})."}

    events: List[dict] = []

    # ── Apply the human's move (and any subsequent extra-turn human moves
    # are NOT auto-applied — extra_turn for the human means we return after
    # the move and let them pick their next card explicitly). ──────────────
    events.append(_apply_one_move(game, loadout, idx))

    # ── Run AI turns until it's human's turn again or game ends ────────────
    safety = 0
    while not game.game_finished() and safety < MAX_TURNS_HARD_CAP:
        cur = int(game.get_state()["current_player"])
        if cur == PLAYER_1:
            break   # human's turn (could be extra turn after human's first move
                    # OR AI just finished and flipped back)
        agent = game.get_current_agent()
        ai_state = game.get_state()
        ai_legal = game.possible_moves(ai_state)
        try:
            ai_move = agent.choose_move(game, ai_state, ai_legal)
        except Exception as e:
            events.append({
                "turn":           int(ai_state.get("turn", 0)),
                "player":         PLAYER_2,
                "card_index":     None,
                "card_played":    None,
                "before_move":    _serialize_state(ai_state),
                "after_raw_move": _serialize_state(ai_state),
                "mechanics":      [],
                "after":          _serialize_state(ai_state),
                "error":          f"AI crashed: {type(e).__name__}: {e}",
            })
            break
        events.append(_apply_one_move(game, loadout, ai_move))
        safety += 1

    final_state = game.get_state()
    finished = game.game_finished()
    return {
        "events":        events,
        "state":         _serialize_state(final_state),
        "legal_moves":   [] if finished else list(game.possible_moves(final_state)),
        "finished":      finished,
        "winner":        game.get_winner(),
        # Refresh the trigger preview now that the game has advanced. Empty
        # if it's no longer P1's turn (extra-turn case where it's still P1
        # is handled correctly because final_state.current_player == 1).
        "hand_triggers": ([] if finished
                          else _predict_hand_triggers(game, loadout, PLAYER_1)),
    }


def get_session_status() -> dict:
    """Inspect the current session (used by the frontend on tab open)."""
    with _LOCK:
        sess = _SESSION
    if sess is None:
        return {"active": False}
    game: CardGame = sess["game"]
    state = game.get_state()
    return {
        "active":        True,
        "simulations":   sess["simulations"],
        "depth":         sess.get("depth", DEFAULT_MINIMAX_DEPTH),
        "agent_type":    sess.get("agent_type", "mcts"),
        "loadout":       _loadout_summary(sess["loadout"]),
        "state":         _serialize_state(state),
        "legal_moves":   [] if game.game_finished() else list(game.possible_moves(state)),
        "finished":      game.game_finished(),
        "winner":        game.get_winner(),
        "hand_triggers": ([] if game.game_finished()
                          else _predict_hand_triggers(game, sess["loadout"], PLAYER_1)),
    }
