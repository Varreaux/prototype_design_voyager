"""
pair_eval.py
============
DesignVoyager, pair lab evaluator.

Runs every singleton mechanic and every pair from the top N card mechanics
in `library_card.json`, plays K MCTS-vs-MCTS games per combo, and ranks
each combo by a composite "goodness" score that blends balance,
decisiveness, both-mechanics-meaningful, length sanity, and clean play.

Designed as a generator that yields progress events so the SSE endpoint
in `web/app.py` can stream them to the dashboard in real time.

Public API:
    iter_pair_eval(top_n=10, games_per_combo=30, simulations=50)
        -> yields dicts of shape {"type": ..., "data": {...}}
"""

from __future__ import annotations

import copy
import itertools
import os
import time
from typing import Dict, Iterator, List, Optional

from card_game import CardGame, _fresh_state, PLAYER_1, PLAYER_2, TARGET_SCORE
from mcts_agent import MCTSAgent, MinimaxAgent

from web.aivai_match import (
    LIBRARY_CARD_PATH,
    MECHANIC_PATCHES,
)
from compile_check import load_mechanic_fn


# ── Per-game runner ──────────────────────────────────────────────────────────

# Hard cap so a single buggy mechanic combo cannot stall the whole eval.
PER_GAME_TURN_CAP = 60


def _load_top_mechanics(top_n: int) -> List[dict]:
    """
    Same ranking the AI vs AI tab uses: aggregate desc, tiebreak by earliest
    iteration. Apply showcase patches if any (currently unused for fresh
    libraries since the alignment gate catches the bug upstream now).
    """
    import json
    if not os.path.exists(LIBRARY_CARD_PATH):
        return []
    try:
        entries = json.load(open(LIBRARY_CARD_PATH))
    except (json.JSONDecodeError, OSError):
        return []

    def sort_key(e):
        agg = (e.get("scores") or {}).get("aggregate", 0.0)
        it  = e.get("iteration", 10_000)
        return (-agg, it)

    out = []
    for e in sorted(entries, key=sort_key):
        if len(out) >= top_n:
            break
        name = e.get("mechanic_name", "unknown")
        code = MECHANIC_PATCHES.get(name) or e.get("python_code", "")
        if not code:
            continue
        try:
            fn = load_mechanic_fn(code)
        except Exception:
            fn = None
        if fn is None:
            continue
        out.append({
            "name":      name,
            "aggregate": (e.get("scores") or {}).get("aggregate", 0.0),
            "fn":        fn,
        })
    return out


def _make_agent(agent_type: str, simulations: int, depth: int):
    """Create one agent instance for the pair-lab match."""
    if (agent_type or "mcts").lower() == "minimax":
        # Lower the per-move wall-clock budget here than in the Play vs AI
        # panel — the pair lab runs hundreds of games, so a tight cap matters.
        return MinimaxAgent(max_depth=int(depth or 4), time_budget_s=2.0)
    return MCTSAgent(simulations=int(simulations or 50))


def _run_one_game(mechanic_fns: List, simulations: int = 50,
                  agent_type: str = "mcts", depth: int = 4) -> Dict:
    """
    Run one AI-vs-AI card game with `mechanic_fns` stacked. Both seats use
    the same agent type and parameters so the result reflects the mechanic
    combo's properties, not an asymmetric matchup.
    Returns a per-game stats dict with: winner, length, scores, fired_counts,
    crashed (bool), hit_cap (bool).
    """
    p1 = _make_agent(agent_type, simulations, depth)
    p2 = _make_agent(agent_type, simulations, depth)
    game = CardGame(
        state=_fresh_state(),
        ai_players={PLAYER_1: p1, PLAYER_2: p2},
        mechanics=list(mechanic_fns),
    )

    fired_counts = [0] * len(mechanic_fns)
    crashed = False
    hit_cap = False

    for _ in range(PER_GAME_TURN_CAP):
        if game.game_finished():
            break
        state = game.get_state()
        try:
            moves = game.possible_moves(state)
            agent = game.get_current_agent()
            move  = agent.choose_move(game, state, moves)
        except Exception:
            crashed = True
            break

        # Re-derive per-mechanic fire attribution by running each mechanic
        # off the post-raw-move state and snapshotting between. This duplicates
        # the work the real perform_move does, but it's the only way to know
        # which mechanic in the stack actually had an effect.
        before_scores = dict(state["scores"])
        before_hands  = {p: list(state["hands"][p]) for p in (1, 2)}

        try:
            game.perform_move(move)
        except Exception:
            crashed = True
            break

        try:
            game.advance_turn()
        except Exception:
            crashed = True
            break

        # Now figure out which mechanics fired by re-applying them off a
        # synthetic raw-move state. Cheaper alternative: use _state_before_mechanics
        # that perform_move stashes on the game object.
        raw = getattr(game, "_state_before_mechanics", None)
        if raw is not None:
            running = copy.deepcopy(raw)
            prev = copy.deepcopy(raw)
            for i, fn in enumerate(mechanic_fns):
                try:
                    res = fn(running)
                    if isinstance(res, dict):
                        running = res
                except Exception:
                    continue
                if (running.get("scores")  != prev.get("scores")
                        or running.get("hands")      != prev.get("hands")
                        or running.get("extra_turn") != prev.get("extra_turn")):
                    fired_counts[i] += 1
                prev = copy.deepcopy(running)
    else:
        hit_cap = True

    final = game.get_state()
    winner = game.get_winner()
    return {
        "winner":       winner,
        "length":       int(final.get("turn", 0)),
        "scores":       {1: int(final["scores"][1]), 2: int(final["scores"][2])},
        "fired_counts": fired_counts,
        "crashed":      crashed,
        "hit_cap":      hit_cap,
    }


# ── Composite goodness scoring ───────────────────────────────────────────────

def _compute_composite(games: List[Dict], n_mechs: int) -> Dict:
    """
    Turn a list of per-game stats into a composite "goodness" score with
    its component breakdown.

    Components (each 0..1):
      clean_play        : fraction of games that did NOT crash and did NOT hit cap
                          (kept in the breakdown for diagnostic purposes, but
                          not part of the composite score because the upstream
                          pipeline already screens out crash-prone mechanics,
                          so this metric is essentially always 1)
      balance           : 1 - |p1_win_rate - p2_win_rate|, computed over decisive games
      decisiveness      : fraction of games that ended with a real winner (someone hit 45)
      both_meaningful   : for pairs, min(per-mechanic fire-rate-by-game). For singletons,
                          single mechanic's fire-rate. Higher is better.
      length_sanity     : 1.0 if avg length in [8, 30], scaled outside that band

    Composite = weighted sum of balance, decisiveness, both_meaningful, length_sanity.
    Weights preserve the original 30/25/20/15 ratio, renormalised to sum to 1.0
    after dropping the clean-play multiplier and baseline term.
    """
    n = len(games)
    if n == 0:
        return {"composite": 0.0, "components": {}, "summary": {}}

    crashed_n = sum(1 for g in games if g["crashed"])
    capped_n  = sum(1 for g in games if g["hit_cap"])
    clean_n   = n - crashed_n - capped_n
    clean_play = clean_n / n

    p1_wins = sum(1 for g in games if g["winner"] == 1)
    p2_wins = sum(1 for g in games if g["winner"] == 2)
    decisive = p1_wins + p2_wins
    decisiveness = decisive / n
    if decisive > 0:
        balance = 1.0 - abs(p1_wins - p2_wins) / decisive
    else:
        balance = 0.0

    # Mechanic engagement: each mechanic should fire in a meaningful fraction
    # of games. We compute fire-rate as fraction of games where the mechanic
    # had any effect at least once.
    if n_mechs > 0:
        per_mech_fire_rate = []
        for i in range(n_mechs):
            fired_in_games = sum(1 for g in games if g["fired_counts"][i] > 0)
            per_mech_fire_rate.append(fired_in_games / n)
        both_meaningful = min(per_mech_fire_rate)
    else:
        per_mech_fire_rate = []
        both_meaningful = 1.0

    avg_length = sum(g["length"] for g in games) / n
    if 8 <= avg_length <= 30:
        length_sanity = 1.0
    elif avg_length < 8:
        length_sanity = max(0.0, avg_length / 8.0)
    else:
        length_sanity = max(0.0, 1.0 - (avg_length - 30) / 30.0)

    # Weights are the original 30/25/20/15 ratio renormalised to sum to 1.0
    # after dropping the clean-play multiplier (always ~1.0 in practice)
    # and the constant baseline term.
    composite = (
        0.33 * balance +
        0.28 * decisiveness +
        0.22 * both_meaningful +
        0.17 * length_sanity
    )

    return {
        "composite": round(composite, 3),
        "components": {
            "clean_play":      round(clean_play, 3),
            "balance":         round(balance, 3),
            "decisiveness":    round(decisiveness, 3),
            "both_meaningful": round(both_meaningful, 3),
            "length_sanity":   round(length_sanity, 3),
        },
        "summary": {
            "p1_wins":            p1_wins,
            "p2_wins":            p2_wins,
            "draws":              decisive and (n - decisive) or n - decisive,
            "crashed":            crashed_n,
            "hit_cap":            capped_n,
            "avg_length":         round(avg_length, 1),
            "per_mech_fire_rate": [round(x, 3) for x in per_mech_fire_rate],
            "n_games":            n,
        },
    }


# ── Top-level streaming runner ───────────────────────────────────────────────

def iter_pair_eval(top_n: int = 10, games_per_combo: int = 30,
                    simulations: int = 50,
                    agent_type: str = "mcts",
                    depth: int = 4) -> Iterator[Dict]:
    """
    Generator. Yields events of the form {"type": str, "data": dict}.

    Args:
        agent_type : "mcts" or "minimax"
        simulations: per-move sims when agent_type='mcts' (default 50)
        depth      : alpha-beta search depth when agent_type='minimax' (default 4).
                     Higher depths multiply runtime quickly: depth 4 is
                     practical for the full pair lab; depth 6+ gets long.

    Event types:
      - "start"        : {"top_n", "games_per_combo", "simulations", "depth",
                          "agent_type", "n_singletons", "n_pairs",
                          "total_combos", "mechanics": [name, ...]}
      - "combo_start"  : {"index", "kind": "single"|"pair", "names": [...],
                          "total_combos"}
      - "combo_progress": {"index", "games_done", "games_total"}
      - "combo_done"   : {"index", "kind", "names", "result": {...}}
      - "all_done"     : {"results": [...sorted by composite desc...]}
      - "error"        : {"message"}
    """
    mechanics = _load_top_mechanics(top_n)
    if not mechanics:
        yield {"type": "error", "data": {"message": "No card mechanics in library."}}
        return
    if len(mechanics) < 2:
        yield {"type": "error", "data": {"message": "Need at least 2 mechanics."}}
        return

    names = [m["name"] for m in mechanics]
    singletons = [(i,) for i in range(len(mechanics))]
    pairs      = list(itertools.combinations(range(len(mechanics)), 2))
    combos = singletons + pairs

    yield {"type": "start", "data": {
        "top_n":           top_n,
        "games_per_combo": games_per_combo,
        "simulations":     simulations,
        "depth":           depth,
        "agent_type":      agent_type,
        "n_singletons":    len(singletons),
        "n_pairs":         len(pairs),
        "total_combos":    len(combos),
        "mechanics":       names,
    }}

    results: List[Dict] = []

    for combo_idx, idx_tuple in enumerate(combos):
        kind = "single" if len(idx_tuple) == 1 else "pair"
        combo_names = [names[i] for i in idx_tuple]
        combo_fns   = [mechanics[i]["fn"] for i in idx_tuple]

        yield {"type": "combo_start", "data": {
            "index":        combo_idx,
            "kind":         kind,
            "names":        combo_names,
            "total_combos": len(combos),
        }}

        per_game_stats: List[Dict] = []
        t0 = time.time()
        for g in range(games_per_combo):
            stats = _run_one_game(combo_fns,
                                  simulations=simulations,
                                  agent_type=agent_type,
                                  depth=depth)
            per_game_stats.append(stats)
            # Emit progress every ~10% to avoid spamming the SSE stream
            if (g + 1) == games_per_combo or (g + 1) % max(1, games_per_combo // 5) == 0:
                yield {"type": "combo_progress", "data": {
                    "index":       combo_idx,
                    "games_done":  g + 1,
                    "games_total": games_per_combo,
                }}

        composite = _compute_composite(per_game_stats, n_mechs=len(idx_tuple))
        elapsed = round(time.time() - t0, 2)

        result = {
            "index":          combo_idx,
            "kind":           kind,
            "names":          combo_names,
            "n_mechanics":    len(idx_tuple),
            "elapsed_sec":    elapsed,
            **composite,
        }
        results.append(result)

        yield {"type": "combo_done", "data": {
            "index":        combo_idx,
            "kind":         kind,
            "names":        combo_names,
            "result":       result,
        }}

    results.sort(key=lambda r: -r["composite"])
    yield {"type": "all_done", "data": {"results": results}}
