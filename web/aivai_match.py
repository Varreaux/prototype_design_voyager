"""
aivai_match.py
==============
DesignVoyager, AI vs AI match runner for the new dashboard tab.

Loads the top N card mechanics from library_card.json by aggregate score,
runs a single MCTS-vs-MCTS card game with all of them stacked, and returns
a structured per-move trace so the frontend can replay it with clear
attribution of which mechanic fired on which move.

Pure Python module. No FastAPI dependency, just data in / data out.
"""

import copy
import json
import os
from typing import Any

from card_game import CardGame, _fresh_state, PLAYER_1, PLAYER_2
from compile_check import load_mechanic_fn
from mcts_agent import MCTSAgent, MinimaxAgent


PROJECT_ROOT      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY_CARD_PATH = os.path.join(PROJECT_ROOT, "library_card.json")

MAX_TURNS_HARD_CAP = 200      # safety so a buggy mechanic combo cannot loop forever
DEFAULT_LOADOUT_N  = 2
DEFAULT_MCTS_SIMS  = 200


# ── Showcase-only patches for known LLM hallucinations ──────────────────────
#
# Some accepted mechanics in library_card.json have a description-vs-code
# mismatch: the code re-adds played_card to the current player's score even
# though CardGame.perform_move already added it. The verifier accepted them
# because metrics still moved, but the doubled scoring makes the AI vs AI
# showcase end in 2 to 3 turns and looks broken to a viewer.
#
# We override the buggy python_code here, only inside the AI vs AI runner,
# so the original library data stays intact for the writeup. Each entry
# below is keyed by mechanic_name and contains a corrected python_code
# string that matches the mechanic's stated description.
MECHANIC_PATCHES = {
    # Original library code discards hand[0] (leftmost) when the mechanic
    # fires, which is unprincipled — sometimes that's the highest card in
    # hand. Patched to drop the *lowest*-value card so the +5 bonus is a
    # net-positive trade in expectation. Net effect: MCTS / minimax now
    # actually plays 1s when they have one to trigger this mechanic.
    "card_discard_bonus": (
        "def card_discard_bonus(game_state):\n"
        "    player = game_state['current_player']\n"
        "    last_played_card = game_state['last_played']\n"
        "\n"
        "    if last_played_card == 1:\n"
        "        hand = game_state['hands'][player]\n"
        "        if len(hand) > 0:\n"
        "            min_idx = min(range(len(hand)), key=lambda i: hand[i])\n"
        "            hand.pop(min_idx)\n"
        "            game_state['scores'][player] += 5\n"
        "\n"
        "    return game_state\n"
    ),
    # Original library code resets extra_turn to False on the else branch,
    # which clobbers any extra_turn set by an earlier mechanic in the loadout
    # (e.g. exact_score_power_up firing on a score of 15 right before this
    # mechanic runs with last_played != 9/10). Patched to only set
    # extra_turn=True on the trigger condition and never touch it otherwise,
    # so it composes cleanly with other mechanics in a stack.
    "high_value_double_play": (
        "def high_value_double_play(game_state):\n"
        "    last_played_card = game_state['last_played']\n"
        "    if last_played_card in [9, 10]:\n"
        "        game_state['extra_turn'] = True\n"
        "    return game_state\n"
    ),
    "reverse_card_penalty": (
        "def reverse_card_penalty(game_state):\n"
        "    current_player = game_state['current_player']\n"
        "    other_player = 1 if current_player == 2 else 2\n"
        "    played_card = game_state['last_played']\n"
        "\n"
        "    if 'custom_state' not in game_state:\n"
        "        game_state['custom_state'] = {}\n"
        "\n"
        "    if played_card is not None and game_state['turn'] > 1:\n"
        "        opponent_last = game_state['custom_state'].get(\n"
        "            f'p{other_player}_last_played_value')\n"
        "        if opponent_last is not None and played_card < opponent_last:\n"
        "            # perform_move already added +played_card; subtract 2x to\n"
        "            # net the described 'subtracted instead of added' effect.\n"
        "            game_state['scores'][current_player] -= 2 * played_card\n"
        "\n"
        "    game_state['custom_state'][f'p{current_player}_last_played_value'] = played_card\n"
        "    return game_state\n"
    ),
    "exact_match_score_reset": (
        "def exact_match_score_reset(game_state):\n"
        "    current_player = game_state['current_player']\n"
        "    opponent_player = 1 if current_player == 2 else 2\n"
        "    played_card = game_state['last_played']\n"
        "\n"
        "    if 'custom_state' not in game_state:\n"
        "        game_state['custom_state'] = {}\n"
        "    if 'last_played_by_player' not in game_state['custom_state']:\n"
        "        game_state['custom_state']['last_played_by_player'] = {1: None, 2: None}\n"
        "\n"
        "    if game_state['turn'] > 1 and played_card is not None:\n"
        "        opp_last = game_state['custom_state']['last_played_by_player'][opponent_player]\n"
        "        if opp_last == played_card:\n"
        "            game_state['scores'][opponent_player] = 0\n"
        "\n"
        "        game_state['custom_state']['last_played_by_player'][current_player] = played_card\n"
        "    return game_state\n"
    ),
}


# ── Mechanic loading ─────────────────────────────────────────────────────────

def load_top_mechanics(n: int = DEFAULT_LOADOUT_N) -> list:
    """
    Read library_card.json, sort by aggregate score descending (tiebreak by
    iteration ascending so ties resolve to the earliest-accepted mechanic),
    take the top N, and load each one's python_code into a callable.

    Returns a list of dicts:
        {
          'name'        : str,
          'description' : str,
          'aggregate'   : float,
          'fn'          : callable(state) -> state,
        }

    Mechanics whose python_code fails to load are skipped.
    """
    if not os.path.exists(LIBRARY_CARD_PATH):
        return []

    with open(LIBRARY_CARD_PATH, "r") as f:
        try:
            entries = json.load(f)
        except json.JSONDecodeError:
            return []

    def sort_key(e):
        agg = (e.get("scores") or {}).get("aggregate", 0.0)
        it  = e.get("iteration", 10_000)
        return (-agg, it)

    entries_sorted = sorted(entries, key=sort_key)

    loaded = []
    for entry in entries_sorted:
        if len(loaded) >= n:
            break
        name = entry.get("mechanic_name", "unknown")
        # If we have a hand-corrected version of this mechanic, prefer it for
        # the showcase. The original code stays in library_card.json untouched.
        code     = MECHANIC_PATCHES.get(name) or entry.get("python_code", "")
        patched  = name in MECHANIC_PATCHES
        if not code:
            continue
        try:
            fn = load_mechanic_fn(code)
        except Exception:
            fn = None
        if fn is None:
            continue
        loaded.append({
            "name":        name,
            "description": entry.get("description", ""),
            "aggregate":   (entry.get("scores") or {}).get("aggregate", 0.0),
            "patched":     patched,
            "fn":          fn,
        })
    return loaded


def load_mechanics_by_name(names: list) -> list:
    """
    Load mechanics from library_card.json matching the given list of names,
    preserving the order in `names`. Mechanics not found, or whose code
    fails to load, are skipped silently. Used when the Pair Lab tab sends
    a specific combo over for a live AI vs AI match.
    """
    import json
    if not names or not os.path.exists(LIBRARY_CARD_PATH):
        return []
    try:
        entries = json.load(open(LIBRARY_CARD_PATH))
    except (json.JSONDecodeError, OSError):
        return []

    by_name = {e.get("mechanic_name"): e for e in entries}
    out = []
    for name in names:
        entry = by_name.get(name)
        if not entry:
            continue
        code    = MECHANIC_PATCHES.get(name) or entry.get("python_code", "")
        patched = name in MECHANIC_PATCHES
        if not code:
            continue
        try:
            fn = load_mechanic_fn(code)
        except Exception:
            fn = None
        if fn is None:
            continue
        out.append({
            "name":        name,
            "description": entry.get("description", ""),
            "aggregate":   (entry.get("scores") or {}).get("aggregate", 0.0),
            "patched":     patched,
            "fn":          fn,
        })
    return out


# ── State helpers ────────────────────────────────────────────────────────────

def _serialize_state(state: dict) -> dict:
    """JSON-safe view of the parts of state the frontend needs."""
    return {
        "hands":          {str(p): list(state["hands"][p]) for p in (PLAYER_1, PLAYER_2)},
        "scores":         {str(p): int(state["scores"][p]) for p in (PLAYER_1, PLAYER_2)},
        "current_player": int(state.get("current_player", PLAYER_1)),
        "turn":           int(state.get("turn", 0)),
        "last_played":    state.get("last_played"),
    }


def _state_changed(before: dict, after: dict) -> dict:
    """
    Compare two card-game states and report whether scores, hands, the
    extra_turn flag, or the custom_state dict changed. Used to decide
    whether a mechanic actually 'fired' on a given move.
    """
    score_changes = {}
    hand_changes  = {}
    for p in (PLAYER_1, PLAYER_2):
        if before["scores"].get(p) != after["scores"].get(p):
            score_changes[str(p)] = {
                "before": int(before["scores"].get(p, 0)),
                "after":  int(after["scores"].get(p, 0)),
            }
        if list(before["hands"].get(p, [])) != list(after["hands"].get(p, [])):
            hand_changes[str(p)] = True

    extra_turn_changed = bool(before.get("extra_turn", False)) != bool(after.get("extra_turn", False))
    custom_changed     = before.get("custom_state", {})  != after.get("custom_state", {})

    # "Fired" means the mechanic had a player-visible effect this turn. Many
    # mechanics write to custom_state every turn just for bookkeeping (e.g.
    # remembering the opponent's last card), so custom_state changes alone
    # don't count.
    fired = bool(score_changes or hand_changes or extra_turn_changed)
    return {
        "fired":              fired,
        "score_changes":      score_changes,
        "hand_changes":       hand_changes,
        "extra_turn_changed": extra_turn_changed,
        "custom_state_changed": custom_changed,
    }


# ── Match runner ─────────────────────────────────────────────────────────────

def run_match(loadout: list = None,
              simulations: int = DEFAULT_MCTS_SIMS) -> dict:
    """
    Play one full MCTS-vs-MCTS card game with every mechanic in `loadout`
    applied (in list order) after every move. Returns a structured trace.

    Args:
        loadout     : list of mechanic dicts as returned by load_top_mechanics().
                      If None, calls load_top_mechanics() with the default N.
        simulations : MCTS simulations per move for both agents.

    Returns:
        {
          'loadout'       : [{'name','description','aggregate'}, ...],
          'simulations'   : int,
          'moves'         : [move_event, ...],
          'winner'        : 1 | 2 | None,
          'final_scores'  : {'1': int, '2': int},
          'total_turns'   : int,
          'hit_safety_cap': bool,
        }

    Each move_event:
        {
          'turn'           : int,
          'player'         : 1 | 2,
          'card_index'     : int,
          'card_played'    : int | None,
          'before_move'    : serialized state (snapshot),
          'after_raw_move' : serialized state (after card played, before mechanics),
          'mechanics'      : [
              {
                'name'              : str,
                'fired'             : bool,
                'score_changes'     : {'1'|'2': {'before','after'}},
                'hand_changes'      : {'1'|'2': True},
                'extra_turn_changed': bool,
                'custom_state_changed': bool,
                'after'             : serialized state (after this mechanic ran),
              },
              ...
          ],
        }
    """
    if loadout is None:
        loadout = load_top_mechanics()

    fns = [m["fn"] for m in loadout]

    # Real game: pass the mechanic functions in so the agent sees them via
    # next_state (otherwise the agent would plan a different game than the
    # one we're showing).
    #
    # AI vs AI showcase is hardcoded to minimax depth 8 — strong enough that
    # the matches showcase the mechanics' real strategic value rather than
    # the noise inherent to low-sim MCTS. The `simulations` arg is now
    # ignored, kept in the signature only so existing API callers don't break.
    p1_agent = MinimaxAgent(max_depth=8, time_budget_s=6.0)
    p2_agent = MinimaxAgent(max_depth=8, time_budget_s=6.0)
    game = CardGame(
        state=_fresh_state(),
        ai_players={PLAYER_1: p1_agent, PLAYER_2: p2_agent},
        mechanics=fns,
    )

    moves: list = []
    hit_cap = False

    for _ in range(MAX_TURNS_HARD_CAP):
        if game.game_finished():
            break

        state = game.get_state()
        player = state["current_player"]
        legal = game.possible_moves(state)
        agent = game.get_current_agent()

        try:
            chosen = agent.choose_move(game, state, legal)
        except Exception as e:
            # If the MCTS planner crashes (probably a mechanic broke during a
            # rollout) we cannot recover this game; record what we have so far.
            moves.append({
                "turn":          int(state.get("turn", 0)),
                "player":        int(player),
                "card_index":    None,
                "card_played":   None,
                "before_move":   _serialize_state(state),
                "after_raw_move": _serialize_state(state),
                "mechanics":     [],
                "error":         f"agent crashed: {type(e).__name__}: {e}",
            })
            break

        before_snapshot = _serialize_state(state)

        # Recompute the "raw move" snapshot manually so we can show the user the
        # card-played effect on its own, before any mechanic touches the state.
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

        # Walk the mechanics one at a time off the raw_state, snapshotting
        # between each one. This is for replay only; the real game still runs
        # them atomically inside perform_move below.
        per_mech_events = []
        running = copy.deepcopy(raw_state)
        prev = copy.deepcopy(raw_state)
        for mech in loadout:
            fn = mech["fn"]
            try:
                result = fn(running)
                if isinstance(result, dict):
                    running = result
            except Exception as e:
                # A mechanic crash on the real move; record it but keep going.
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

        # Now actually advance the real game (also runs the mechanics, real state stays in sync).
        try:
            game.perform_move(chosen)
            game.advance_turn()
        except Exception as e:
            moves.append({
                "turn":          int(state.get("turn", 0)),
                "player":        int(player),
                "card_index":    int(chosen) if isinstance(chosen, int) else None,
                "card_played":   card_played,
                "before_move":   before_snapshot,
                "after_raw_move": after_raw_snapshot,
                "mechanics":     per_mech_events,
                "error":         f"perform_move crashed: {type(e).__name__}: {e}",
            })
            break

        moves.append({
            "turn":          int(state.get("turn", 0)),
            "player":        int(player),
            "card_index":    int(chosen) if isinstance(chosen, int) else None,
            "card_played":   card_played,
            "before_move":   before_snapshot,
            "after_raw_move": after_raw_snapshot,
            "mechanics":     per_mech_events,
        })
    else:
        hit_cap = True

    final_state = game.get_state()
    return {
        "loadout": [
            {
                "name":        m["name"],
                "description": m["description"],
                "aggregate":   m["aggregate"],
                "patched":     m.get("patched", False),
            }
            for m in loadout
        ],
        "simulations":    simulations,
        "moves":          moves,
        "winner":         game.get_winner(),
        "final_scores":   {"1": int(final_state["scores"][PLAYER_1]),
                           "2": int(final_state["scores"][PLAYER_2])},
        "total_turns":    int(final_state.get("turn", 0)),
        "hit_safety_cap": hit_cap,
    }
