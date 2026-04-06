"""
playtest_module.py
==================
DesignVoyager — Playtest Module

Step 3 of the loop: runs automated games to measure how good the
mechanic is. Uses pure Python agents — NO OpenAI API calls here.

Measures three things (same as the paper):
  1. Playability  — what fraction of games finish normally?
  2. Balance      — how even are the win rates? (lower gap = fairer)
  3. Depth        — does skill matter? (greedy beats random more = deeper)

Game-agnostic: callers pass game_class (any GameInterface subclass).
Defaults to BaseGame so existing callers don't break.
"""

import signal
import concurrent.futures
import numpy as np
from base_game import BaseGame
from compile_check import load_mechanic_fn

# How many games to run per measurement
N_GAMES_BALANCE = 60   # for playability + balance (reduced, MCTS games are slower)
N_GAMES_DEPTH   = 40   # for strategic depth (reduced, MCTS games are slower)
MAX_TURNS       = 100  # safety cap, gives complex mechanics more room to resolve
GAME_TIMEOUT    = 10   # wall-clock seconds per game (int required by signal.alarm)


class _GameTimeout(Exception):
    """Raised by the SIGALRM handler when a game exceeds GAME_TIMEOUT seconds."""
    pass


def _timeout_handler(signum, frame):
    raise _GameTimeout()


def run_single_game(mechanic_fn=None, agent1=None, agent2=None,
                    game_class=None) -> tuple:
    """
    Run one game and return (winner, completed_normally).

    Args:
        mechanic_fn : optional Python function to add as a mechanic
        agent1      : GameAgent for player 1 (default: game_class.make_random_agent())
        agent2      : GameAgent for player 2 (default: game_class.make_random_agent())
        game_class  : GameInterface subclass to instantiate
                      (default: BaseGame for backwards compatibility)

    Returns:
        (winner: int or None, completed: bool)
        winner=None means draw or did not complete
    """
    game_class = game_class or BaseGame
    agent1     = agent1 or game_class.make_random_agent()
    agent2     = agent2 or game_class.make_random_agent()

    # Use the GameInterface factory so this works for any game type
    game = game_class.create(mechanic_fn=mechanic_fn, agent1=agent1, agent2=agent2)

    for _ in range(MAX_TURNS):
        state = game.get_state()
        moves = game.possible_moves(state)

        if not moves:
            return None, True   # Draw — no moves left

        agent = game.get_current_agent()
        move  = agent.choose_move(game, state, moves)

        if not game.is_valid_move(move):
            return None, False  # Invalid move = broken mechanic

        game.perform_move(move)

        if game.game_finished():
            winner = game.get_winner()
            return winner, True

        game.advance_turn()

    return None, False   # Safety: didn't finish in MAX_TURNS


def _run_game_safe(mechanic_fn, agent1, agent2, game_class=None,
                   use_signal=True) -> tuple:
    """
    Run run_single_game() with a hard timeout.

    When use_signal=True (default, CLI mode):
        Uses SIGALRM which fires at the OS level and interrupts even C
        extensions (numpy, etc.) that hold the GIL. Requires Unix/macOS
        and must be called from the main thread.

    When use_signal=False (web server mode):
        Uses ThreadPoolExecutor with a timeout. Works from any thread
        but cannot interrupt GIL-holding C code. Good enough for most
        mechanics and avoids the main-thread restriction.
    """
    if use_signal:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(GAME_TIMEOUT)
        try:
            return run_single_game(mechanic_fn, agent1, agent2, game_class=game_class)
        except _GameTimeout:
            return None, False
        except Exception:
            return None, False
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run_single_game, mechanic_fn, agent1, agent2, game_class)
            try:
                return future.result(timeout=GAME_TIMEOUT)
            except (concurrent.futures.TimeoutError, Exception):
                return None, False


def measure_playability_and_balance(mechanic_fn=None, game_class=None,
                                    use_signal=True) -> tuple:
    """
    Run N games with two equal MCTS agents (simulations=20).

    Args:
        mechanic_fn : optional mechanic function
        game_class  : GameInterface subclass (default: BaseGame)
        use_signal  : True for SIGALRM timeouts (CLI), False for thread-based (web)

    Returns:
        playability : fraction of games that completed normally (0.0 - 1.0)
        balance_gap : |P1_wins - P2_wins| / completed_games (0.0 = perfect balance)
    """
    game_class = game_class or BaseGame
    completed  = 0
    p1_wins    = 0
    p2_wins    = 0

    for _ in range(N_GAMES_BALANCE):
        a1     = game_class.make_mcts_agent(simulations=20)
        a2     = game_class.make_mcts_agent(simulations=20)
        winner, ok = _run_game_safe(mechanic_fn, a1, a2, game_class=game_class,
                                    use_signal=use_signal)
        if not ok:
            # Any single failure means mechanic is unplayable, stop early
            return 0.0, 1.0
        completed += 1
        if winner == 1:
            p1_wins += 1
        elif winner == 2:
            p2_wins += 1

    playability = completed / N_GAMES_BALANCE
    balance_gap = abs(p1_wins - p2_wins) / completed
    return playability, balance_gap


def measure_depth(mechanic_fn=None, game_class=None, use_signal=True) -> float:
    """
    Run N games: strong MCTS (50 sims) vs weak MCTS (10 sims).
    Alternates seats each game so seat advantage doesn't skew results.
    A higher strong-agent win rate means the game rewards better play
    (i.e. more strategic depth).

    Args:
        mechanic_fn : optional mechanic function
        game_class  : GameInterface subclass (default: BaseGame)
        use_signal  : True for SIGALRM timeouts (CLI), False for thread-based (web)

    Returns:
        depth_proxy : strong agent win rate (0.0 - 1.0)
    """
    game_class   = game_class or BaseGame
    strong_wins  = 0
    completed    = 0

    for i in range(N_GAMES_DEPTH):
        strong = game_class.make_mcts_agent(simulations=50)
        weak   = game_class.make_mcts_agent(simulations=10)

        # Alternate seats: even games strong=P1, odd games strong=P2
        if i % 2 == 0:
            a1, a2 = strong, weak
            strong_player = 1
        else:
            a1, a2 = weak, strong
            strong_player = 2

        winner, ok = _run_game_safe(mechanic_fn, a1, a2, game_class=game_class,
                                    use_signal=use_signal)
        if ok:
            completed += 1
            if winner == strong_player:
                strong_wins += 1

    if completed == 0:
        return 0.0

    return strong_wins / completed


def playtest(mechanic: dict, game_class=None, use_signal=True) -> dict:
    """
    Full playtest of a mechanic. Runs automated games and returns scores.

    Args:
        mechanic   : dict from proposal_module (must have 'python_code')
        game_class : GameInterface subclass to use for playtesting
                     (default: BaseGame)
        use_signal : True for SIGALRM timeouts (CLI), False for thread-based (web)

    Returns:
        scores dict with keys:
            playability  (higher is better, target >= 0.8)
            balance_gap  (lower is better, target <= 0.4)
            depth        (higher is better)
            aggregate    (combined score for ranking)
    """
    game_class = game_class or BaseGame
    name       = mechanic.get("mechanic_name", "unknown")
    code       = mechanic.get("python_code", "")

    print(f"[Playtest] Running games for '{name}'...")

    mechanic_fn = load_mechanic_fn(code)

    playability, balance_gap = measure_playability_and_balance(mechanic_fn, game_class,
                                                               use_signal=use_signal)
    depth                    = measure_depth(mechanic_fn, game_class,
                                            use_signal=use_signal)

    # Aggregate score — playability is a hard binary gate in verification,
    # so it is excluded here to avoid inflating scores.
    # Balance and depth each carry 50% weight.
    aggregate = (
        0.5 * (1.0 - balance_gap) +
        0.5 * depth
    )

    scores = {
        "playability":  round(playability, 3),
        "balance_gap":  round(balance_gap, 3),
        "depth":        round(depth, 3),
        "aggregate":    round(aggregate, 3),
    }

    print(f"  [Playtest] playability={scores['playability']:.2f}  "
          f"balance_gap={scores['balance_gap']:.2f}  "
          f"depth={scores['depth']:.2f}  "
          f"aggregate={scores['aggregate']:.2f}")

    return scores


# ── Replay recording ─────────────────────────────────────────────────────────

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

    Args:
        mechanic_fn : optional mechanic function to apply each turn
        game_class  : GameInterface subclass (default: BaseGame)
        agent1      : agent for player 1 (default: MCTS 50 sims)
        agent2      : agent for player 2 (default: MCTS 50 sims)

    Returns a dict with:
        winner        : int or None
        completed     : bool
        turns         : total moves played
        initial_state : serialized starting state
        moves         : list of {turn, player, move, state_after}
    """
    import copy
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

        # Convert move to something JSON-safe (could be int or string)
        safe_move = move if isinstance(move, (int, float, str, bool)) else str(move)

        # Capture the state before mechanics were applied (set during perform_move)
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
