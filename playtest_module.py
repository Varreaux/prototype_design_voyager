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

import copy
import signal
import numpy as np
from base_game import BaseGame
from compile_check import load_mechanic_fn

# How many games to run per measurement
N_GAMES_BALANCE = 200  # for playability + balance
N_GAMES_DEPTH   = 100  # for strategic depth
MAX_TURNS       = 100  # safety cap — gives complex mechanics more room to resolve
GAME_TIMEOUT    = 4    # wall-clock seconds per game (int required by signal.alarm)


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


def _run_game_safe(mechanic_fn, agent1, agent2, game_class=None) -> tuple:
    """
    Run run_single_game() with a SIGALRM-based hard timeout.

    Unlike the threading approach, SIGALRM fires at the OS level and
    interrupts even C extensions (numpy, etc.) that hold the GIL —
    which is exactly what happens when a mechanic calls np.random.choice
    on an empty array and hangs inside numpy's C code.

    Requires Unix/macOS (not Windows). signal.alarm must be called from
    the main thread, which is always the case here since playtest() is
    called from the main loop.
    """
    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(GAME_TIMEOUT)
    try:
        return run_single_game(mechanic_fn, agent1, agent2, game_class=game_class)
    except _GameTimeout:
        return None, False   # Timed out — treat as incomplete game
    except Exception:
        return None, False   # Any other crash — treat as incomplete
    finally:
        signal.alarm(0)                                  # Cancel pending alarm
        signal.signal(signal.SIGALRM, old_handler)       # Restore previous handler


def measure_playability_and_balance(mechanic_fn=None, game_class=None) -> tuple:
    """
    Run N games with two random agents.

    Args:
        mechanic_fn : optional mechanic function
        game_class  : GameInterface subclass (default: BaseGame)

    Returns:
        playability : fraction of games that completed normally (0.0 - 1.0)
        balance_gap : |P1_wins - P2_wins| / completed_games (0.0 = perfect balance)
    """
    game_class = game_class or BaseGame
    completed  = 0
    p1_wins    = 0
    p2_wins    = 0

    for _ in range(N_GAMES_BALANCE):
        r1     = game_class.make_random_agent()
        r2     = game_class.make_random_agent()
        winner, ok = _run_game_safe(mechanic_fn, r1, r2, game_class=game_class)
        if not ok:
            # Any single failure means mechanic is unplayable — stop early
            return 0.0, 1.0
        completed += 1
        if winner == 1:
            p1_wins += 1
        elif winner == 2:
            p2_wins += 1

    playability = completed / N_GAMES_BALANCE
    balance_gap = abs(p1_wins - p2_wins) / completed
    return playability, balance_gap


def measure_depth(mechanic_fn=None, game_class=None) -> float:
    """
    Run N games: GreedyAgent vs RandomAgent.
    A higher greedy win rate suggests the game rewards better decisions
    (i.e. more strategic depth).

    Args:
        mechanic_fn : optional mechanic function
        game_class  : GameInterface subclass (default: BaseGame)

    Returns:
        depth_proxy : greedy win rate (0.0 - 1.0)
    """
    game_class  = game_class or BaseGame
    greedy_wins = 0
    completed   = 0

    for _ in range(N_GAMES_DEPTH):
        # Greedy plays as player 1
        g1     = game_class.make_greedy_agent()
        r2     = game_class.make_random_agent()
        winner, ok = _run_game_safe(mechanic_fn, g1, r2, game_class=game_class)
        if ok:
            completed += 1
            if winner == 1:
                greedy_wins += 1

    if completed == 0:
        return 0.0

    return greedy_wins / completed


def playtest(mechanic: dict, game_class=None) -> dict:
    """
    Full playtest of a mechanic. Runs automated games and returns scores.

    Args:
        mechanic   : dict from proposal_module (must have 'python_code')
        game_class : GameInterface subclass to use for playtesting
                     (default: BaseGame)

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

    playability, balance_gap = measure_playability_and_balance(mechanic_fn, game_class)
    depth                    = measure_depth(mechanic_fn, game_class)

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
