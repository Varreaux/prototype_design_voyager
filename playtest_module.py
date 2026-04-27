"""
playtest_module.py
==================
DesignVoyager — Playtest Module

Step 3 of the loop: runs automated games to measure how good the
mechanic is. Uses pure Python agents — NO LLM API calls here.

Two layers of API:

  1. Legacy simple API (preserved for the library and existing callers):
     - playtest(mechanic, game_class) -> {'playability', 'balance_gap',
       'depth', 'aggregate'}

  2. Full delta-gated API (new, used by SelfVerifier):
     - run_baseline(game_class) -> PlaytestMetrics
     - run_playtest_full(mechanic, game_class)
           -> (PlaytestMetrics, TriggerStats, simple_scores_dict)

Game-agnostic: callers pass game_class (any GameInterface subclass).
Defaults to BaseGame so existing callers don't break.
"""

import copy
import json
import signal
import concurrent.futures
import numpy as np
from base_game import BaseGame
from compile_check import load_mechanic_fn
from verification_schema import PlaytestMetrics, TriggerStats

# How many games to run per measurement
N_GAMES_BALANCE = 60   # equal MCTS vs equal MCTS, for playability + balance
N_GAMES_DEPTH   = 40   # strong (50 sims) vs weak (10 sims) MCTS, for depth
MAX_TURNS       = 100  # safety cap per game
GAME_TIMEOUT    = 10   # wall-clock seconds per game (int required by signal.alarm)


# ── Trigger tracking ──────────────────────────────────────────────────────────

class _TriggerTracker:
    """Counts how often the mechanic ran and whether it actually changed state.

    Per-match counters
    ------------------
    triggered_matches:        # matches where the mechanic_fn was invoked at
                              least once. Pinned at total_matches in this
                              codebase because perform_move calls the mechanic
                              every turn. Kept for the verifier's behavioral
                              gate and back-compat, but no longer surfaced
                              in the UI.
    state_changed_matches:    # matches where the mechanic actually changed
                              the state at least once. This is the honest
                              "trigger rate" a game designer cares about:
                              a mechanic whose condition never fires shows 0.
    """

    def __init__(self):
        self.trigger_count = 0
        self.state_changed_count = 0
        self.triggered_matches = 0
        self.state_changed_matches = 0
        self._this_match_triggered = False
        self._this_match_state_changed = False

    def begin_match(self):
        self._this_match_triggered = False
        self._this_match_state_changed = False

    def end_match(self):
        if self._this_match_triggered:
            self.triggered_matches += 1
        if self._this_match_state_changed:
            self.state_changed_matches += 1


def _state_snapshot(state: dict) -> str:
    """JSON-stringify the parts of state that mechanics can affect."""
    snap = {}
    if 'board' in state and state['board'] is not None:
        b = state['board']
        snap['board'] = b.tolist() if hasattr(b, 'tolist') else list(b)
    if 'hands' in state:
        snap['hands'] = {str(k): list(v) for k, v in state['hands'].items()}
    if 'scores' in state:
        snap['scores'] = {str(k): v for k, v in state['scores'].items()}
    if 'custom_state' in state:
        snap['custom_state'] = state['custom_state']
    if 'extra_turn' in state:
        snap['extra_turn'] = state['extra_turn']
    if 'last_played' in state:
        snap['last_played'] = state['last_played']
    try:
        return json.dumps(snap, default=str, sort_keys=True)
    except Exception:
        return repr(snap)


def _wrap_mechanic_with_tracker(mechanic_fn, tracker: _TriggerTracker):
    """Return a mechanic function that records trigger and state-change stats."""
    if mechanic_fn is None:
        return None

    def tracked(state):
        before = _state_snapshot(state)
        result = mechanic_fn(state)
        tracker.trigger_count += 1
        tracker._this_match_triggered = True
        try:
            after = _state_snapshot(result if result is not None else state)
        except Exception:
            after = before
        if after != before:
            tracker.state_changed_count += 1
            tracker._this_match_state_changed = True
        return result

    tracked.__name__ = getattr(mechanic_fn, "__name__", "tracked_mechanic")
    return tracked


# ── Timeouts ──────────────────────────────────────────────────────────────────

class _GameTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _GameTimeout()


# ── Per-game runner with rich stats ───────────────────────────────────────────

def _empty_game_stats() -> dict:
    return {
        "completed":         False,
        "winner":            None,
        "turns":             0,
        "multi_choice_turns": 0,
        "legal_actions_sum": 0,
        "covered_cells":     set(),
    }


def _track_legal_actions(state: dict, n_moves: int, stats: dict):
    stats["legal_actions_sum"] += n_moves
    if n_moves > 1:
        stats["multi_choice_turns"] += 1


def _track_board_coverage(state: dict, stats: dict):
    """Record which board cells are non-blank. Card game has no board, so no-op."""
    board = state.get('board')
    if board is None:
        return
    arr = board if isinstance(board, np.ndarray) else np.asarray(board)
    nonblank = np.argwhere(arr != '_')
    for (r, c) in nonblank:
        stats["covered_cells"].add((int(r), int(c)))


def run_single_game_with_stats(mechanic_fn=None, agent1=None, agent2=None,
                               game_class=None) -> dict:
    """
    Run one game and return a stats dict with:
        completed, winner, turns, multi_choice_turns, legal_actions_sum,
        covered_cells (set of (r, c)).
    """
    game_class = game_class or BaseGame
    agent1     = agent1 or game_class.make_random_agent()
    agent2     = agent2 or game_class.make_random_agent()

    game  = game_class.create(mechanic_fn=mechanic_fn, agent1=agent1, agent2=agent2)
    stats = _empty_game_stats()

    for _ in range(MAX_TURNS):
        state = game.get_state()
        moves = game.possible_moves(state)
        _track_board_coverage(state, stats)

        if not moves:
            stats["completed"] = True
            stats["winner"]    = None  # draw
            return stats

        _track_legal_actions(state, len(moves), stats)

        agent = game.get_current_agent()
        move  = agent.choose_move(game, state, moves)

        if not game.is_valid_move(move):
            stats["completed"] = False
            return stats

        game.perform_move(move)
        stats["turns"] += 1

        if game.game_finished():
            stats["completed"] = True
            stats["winner"]    = game.get_winner()
            _track_board_coverage(game.get_state(), stats)
            return stats

        game.advance_turn()

    return stats  # didn't finish in MAX_TURNS


def _run_game_with_stats_safe(mechanic_fn, agent1, agent2, game_class=None,
                              use_signal=True) -> dict:
    """run_single_game_with_stats with a hard timeout."""
    if use_signal:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(GAME_TIMEOUT)
        try:
            return run_single_game_with_stats(mechanic_fn, agent1, agent2,
                                              game_class=game_class)
        except (_GameTimeout, Exception):
            return _empty_game_stats()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_single_game_with_stats,
                                 mechanic_fn, agent1, agent2, game_class)
            try:
                return future.result(timeout=GAME_TIMEOUT)
            except (concurrent.futures.TimeoutError, Exception):
                return _empty_game_stats()


# ── Full playtest: balance + depth in one pass, with rich metrics ─────────────

def _accumulate(out: dict, gstats: dict):
    """Aggregate per-game stats into the running totals dict."""
    out["total_matches"] += 1
    if gstats["completed"]:
        out["completed_matches"] += 1
        if gstats["winner"] is None:
            out["draws"] += 1
    out["total_turns"]        += gstats["turns"]
    out["multi_choice_turns"] += gstats["multi_choice_turns"]
    out["legal_actions_sum"]  += gstats["legal_actions_sum"]
    out["covered_cells"].update(gstats["covered_cells"])


def _board_cell_count(game_class) -> int:
    """Number of cells in the board for coverage normalization (0 if no board)."""
    try:
        dummy = game_class.create()
        state = dummy.get_state()
        board = state.get('board')
        if board is None:
            return 0
        arr = board if isinstance(board, np.ndarray) else np.asarray(board)
        return int(arr.size)
    except Exception:
        return 0


def _run_balance_phase(mechanic_fn, game_class, use_signal, tracker,
                       progress_cb=None, phase_label="balance"):
    """Run N_GAMES_BALANCE games of equal-strength MCTS. Returns running totals.

    progress_cb(phase, completed, total) is called every game so the UI can
    update a loading bar.
    """
    out = {
        "total_matches": 0, "completed_matches": 0, "draws": 0,
        "p1_wins": 0, "p2_wins": 0,
        "total_turns": 0, "multi_choice_turns": 0,
        "legal_actions_sum": 0, "covered_cells": set(),
    }
    for i in range(N_GAMES_BALANCE):
        a1 = game_class.make_mcts_agent(simulations=20)
        a2 = game_class.make_mcts_agent(simulations=20)
        if tracker is not None:
            tracker.begin_match()
        gstats = _run_game_with_stats_safe(mechanic_fn, a1, a2,
                                           game_class=game_class,
                                           use_signal=use_signal)
        if tracker is not None:
            tracker.end_match()
        _accumulate(out, gstats)
        if gstats["completed"]:
            if gstats["winner"] == 1: out["p1_wins"] += 1
            elif gstats["winner"] == 2: out["p2_wins"] += 1
        if progress_cb is not None:
            try: progress_cb(phase_label, i + 1, N_GAMES_BALANCE)
            except Exception: pass
    return out


def _run_depth_phase(mechanic_fn, game_class, use_signal, tracker,
                     progress_cb=None, phase_label="depth"):
    """
    Run N_GAMES_DEPTH games: strong (50 sims) vs weak (10 sims), alternating
    seats. Returns running totals plus strong/weak win counts.
    """
    out = {
        "total_matches": 0, "completed_matches": 0, "draws": 0,
        "strong_wins": 0, "weak_wins": 0,
        "total_turns": 0, "multi_choice_turns": 0,
        "legal_actions_sum": 0, "covered_cells": set(),
    }
    for i in range(N_GAMES_DEPTH):
        strong = game_class.make_mcts_agent(simulations=50)
        weak   = game_class.make_mcts_agent(simulations=10)
        if i % 2 == 0:
            a1, a2 = strong, weak
            strong_player = 1
        else:
            a1, a2 = weak, strong
            strong_player = 2

        if tracker is not None:
            tracker.begin_match()
        gstats = _run_game_with_stats_safe(mechanic_fn, a1, a2,
                                           game_class=game_class,
                                           use_signal=use_signal)
        if tracker is not None:
            tracker.end_match()
        _accumulate(out, gstats)
        if gstats["completed"] and gstats["winner"] is not None:
            if gstats["winner"] == strong_player: out["strong_wins"] += 1
            else: out["weak_wins"] += 1
        if progress_cb is not None:
            try: progress_cb(phase_label, i + 1, N_GAMES_DEPTH)
            except Exception: pass
    return out


def _build_metrics(balance: dict, depth: dict, board_cell_count: int) -> PlaytestMetrics:
    """Combine per-phase totals into a single PlaytestMetrics object."""
    total_matches    = balance["total_matches"] + depth["total_matches"]
    completed        = balance["completed_matches"] + depth["completed_matches"]
    draws            = balance["draws"] + depth["draws"]
    total_turns      = balance["total_turns"] + depth["total_turns"]
    multi_choice     = balance["multi_choice_turns"] + depth["multi_choice_turns"]
    legal_actions    = balance["legal_actions_sum"] + depth["legal_actions_sum"]
    covered_cells    = balance["covered_cells"] | depth["covered_cells"]

    bal_completed = max(balance["completed_matches"], 1)
    p1_rate = balance["p1_wins"] / bal_completed
    p2_rate = balance["p2_wins"] / bal_completed

    dep_completed = max(depth["completed_matches"], 1)
    strong_rate = depth["strong_wins"] / dep_completed
    weak_rate   = depth["weak_wins"]   / dep_completed

    draw_rate       = (draws / max(completed, 1))
    avg_game_length = (total_turns / max(completed, 1))
    avg_legal       = (legal_actions / max(total_turns, 1))

    return PlaytestMetrics(
        total_matches=total_matches,
        completed_matches=completed,
        p1_win_rate=p1_rate,
        p2_win_rate=p2_rate,
        strong_agent_win_rate=strong_rate,
        weak_agent_win_rate=weak_rate,
        draw_rate=draw_rate,
        avg_game_length=avg_game_length,
        avg_legal_actions=avg_legal,
        multi_choice_turns=multi_choice,
        total_turns=total_turns,
        covered_cells=len(covered_cells),
        board_cell_count=board_cell_count,
    )


def _simple_scores_from_metrics(metrics: PlaytestMetrics) -> dict:
    """Produce the legacy {playability, balance_gap, depth, aggregate} dict
    used by the library and the existing UI score bars."""
    playability = (metrics.completed_matches / metrics.total_matches) if metrics.total_matches else 0.0
    balance_gap = abs(metrics.p1_win_rate - metrics.p2_win_rate)
    depth_raw   = metrics.strong_agent_win_rate - metrics.weak_agent_win_rate
    depth       = max(0.0, min(1.0, depth_raw))
    aggregate   = 0.5 * (1.0 - balance_gap) + 0.5 * depth
    return {
        "playability": round(playability, 3),
        "balance_gap": round(balance_gap, 3),
        "depth":       round(depth, 3),
        "aggregate":   round(aggregate, 3),
    }


# ── Public API ────────────────────────────────────────────────────────────────

def run_baseline(game_class=None, use_signal=True, progress_cb=None) -> PlaytestMetrics:
    """
    Run a baseline playtest with NO mechanic. The resulting PlaytestMetrics
    object is the parent metrics that SelfVerifier compares mechanic runs
    against.

    progress_cb(phase, completed, total) is called once per game so a UI
    can show a loading bar (phases: 'balance' then 'depth').
    """
    game_class = game_class or BaseGame
    bal = _run_balance_phase(None, game_class, use_signal, tracker=None,
                             progress_cb=progress_cb, phase_label="balance")
    dep = _run_depth_phase(None, game_class, use_signal, tracker=None,
                           progress_cb=progress_cb, phase_label="depth")
    return _build_metrics(bal, dep, _board_cell_count(game_class))


def run_playtest_full(mechanic: dict, game_class=None, use_signal=True,
                      progress_cb=None) -> tuple:
    """
    Full delta-gated playtest. Runs balance + depth phases with the mechanic
    and tracks trigger stats.

    progress_cb(phase, completed, total) is called once per game.

    Returns:
        (child_metrics: PlaytestMetrics,
         trigger_stats: TriggerStats,
         simple_scores: dict)   # legacy {playability, balance_gap, depth, aggregate}
    """
    game_class = game_class or BaseGame
    name = mechanic.get("mechanic_name", "unknown")
    code = mechanic.get("python_code", "")
    print(f"[Playtest] Running games for '{name}'...")

    raw_fn  = load_mechanic_fn(code)
    tracker = _TriggerTracker()
    fn      = _wrap_mechanic_with_tracker(raw_fn, tracker)

    bal = _run_balance_phase(fn, game_class, use_signal, tracker=tracker,
                             progress_cb=progress_cb, phase_label="balance")
    dep = _run_depth_phase(fn, game_class, use_signal, tracker=tracker,
                           progress_cb=progress_cb, phase_label="depth")
    metrics = _build_metrics(bal, dep, _board_cell_count(game_class))

    total_matches = bal["total_matches"] + dep["total_matches"]
    total_turns   = bal["total_turns"]   + dep["total_turns"]
    trigger_stats = TriggerStats(
        trigger_count=tracker.trigger_count,
        triggered_matches=tracker.triggered_matches,
        total_matches=total_matches,
        total_turns=total_turns,
        state_changed_by_mechanic_count=tracker.state_changed_count,
        state_changed_matches=tracker.state_changed_matches,
    )

    scores = _simple_scores_from_metrics(metrics)
    print(f"  [Playtest] playability={scores['playability']:.2f}  "
          f"balance_gap={scores['balance_gap']:.2f}  "
          f"depth={scores['depth']:.2f}  "
          f"aggregate={scores['aggregate']:.2f}  "
          f"trigger_rate={trigger_stats.trigger_rate_by_match():.0%}")
    return metrics, trigger_stats, scores


# ── Legacy thin wrappers (preserved for backwards compatibility) ──────────────

def run_single_game(mechanic_fn=None, agent1=None, agent2=None,
                    game_class=None) -> tuple:
    """
    Legacy: returns just (winner, completed). New code should call
    run_single_game_with_stats() instead.
    """
    s = run_single_game_with_stats(mechanic_fn, agent1, agent2,
                                   game_class=game_class)
    return s["winner"], s["completed"]


def measure_playability_and_balance(mechanic_fn=None, game_class=None,
                                    use_signal=True) -> tuple:
    """Legacy: returns (playability, balance_gap)."""
    bal = _run_balance_phase(mechanic_fn, game_class or BaseGame,
                             use_signal, tracker=None)
    completed = bal["completed_matches"]
    if completed == 0:
        return 0.0, 1.0
    playability = completed / N_GAMES_BALANCE
    balance_gap = abs(bal["p1_wins"] - bal["p2_wins"]) / completed
    return playability, balance_gap


def measure_depth(mechanic_fn=None, game_class=None, use_signal=True) -> float:
    """Legacy: returns the strong-agent win rate as a depth proxy."""
    dep = _run_depth_phase(mechanic_fn, game_class or BaseGame,
                           use_signal, tracker=None)
    if dep["completed_matches"] == 0:
        return 0.0
    return dep["strong_wins"] / dep["completed_matches"]


def playtest(mechanic: dict, game_class=None, use_signal=True) -> dict:
    """
    Legacy simple API. Internally runs the same full playtest as
    run_playtest_full() but returns only the simple scores dict so that
    library storage and existing callers keep working unchanged.
    """
    _, _, scores = run_playtest_full(mechanic, game_class=game_class,
                                     use_signal=use_signal)
    return scores


# ── Replay recording (unchanged) ──────────────────────────────────────────────

def serialize_state(state: dict) -> dict:
    """
    Convert a game state dict into a JSON-safe version.
    Numpy arrays become nested lists; everything else passes through.
    """
    out = {}
    for key, val in state.items():
        if isinstance(val, np.ndarray):
            out[key] = val.tolist()
        else:
            out[key] = val
    return out


def run_single_game_recorded(mechanic_fn=None, game_class=None,
                              agent1=None, agent2=None) -> dict:
    """
    Play one game and record every move and board state for animated replay.
    Unchanged from previous behavior.
    """
    game_class = game_class or BaseGame
    agent1 = agent1 or game_class.make_mcts_agent(simulations=50)
    agent2 = agent2 or game_class.make_mcts_agent(simulations=50)
    game   = game_class.create(mechanic_fn=mechanic_fn, agent1=agent1, agent2=agent2)

    initial_state = serialize_state(game.get_state())
    move_log = []
    turn_count = 0

    for _ in range(MAX_TURNS):
        state = game.get_state()
        moves = game.possible_moves(state)

        if not moves:
            return {
                "winner": None, "completed": True,
                "turns": turn_count, "initial_state": initial_state,
                "moves": move_log,
            }

        agent  = game.get_current_agent()
        player = state.get("current_player", None)
        move   = agent.choose_move(game, state, moves)

        if not game.is_valid_move(move):
            return {
                "winner": None, "completed": False,
                "turns": turn_count, "initial_state": initial_state,
                "moves": move_log,
            }

        game.perform_move(move)
        turn_count += 1

        safe_move = move if isinstance(move, (int, float, str, bool)) else str(move)

        state_before_mech = None
        if hasattr(game, '_state_before_mechanics') and game._state_before_mechanics is not None:
            state_before_mech = serialize_state(game._state_before_mechanics)

        move_log.append({
            "turn":                   turn_count,
            "player":                 player,
            "move":                   safe_move,
            "state_before_mechanics": state_before_mech,
            "state_after":            serialize_state(game.get_state()),
        })

        if game.game_finished():
            return {
                "winner": game.get_winner(), "completed": True,
                "turns": turn_count, "initial_state": initial_state,
                "moves": move_log,
            }

        game.advance_turn()

    return {
        "winner": None, "completed": False,
        "turns": turn_count, "initial_state": initial_state,
        "moves": move_log,
    }
