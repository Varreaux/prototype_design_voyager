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
"""

import copy
import signal
import numpy as np
from boardwalk import Board
from base_game import BaseGame, RandomAgent, GreedyAgent, BOARD_SIZE, PLAYER_1, PLAYER_2
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


def run_single_game(mechanic_fn=None, agent1=None, agent2=None) -> tuple:
    """
    Run one game and return (winner, completed_normally).

    Args:
        mechanic_fn : optional Python function to add as a mechanic
        agent1      : AIPlayer for PLAYER_1 (default: RandomAgent)
        agent2      : AIPlayer for PLAYER_2 (default: RandomAgent)

    Returns:
        (winner: int or None, completed: bool)
        winner=None means draw or did not complete
    """
    agent1 = agent1 or RandomAgent()
    agent2 = agent2 or RandomAgent()

    board    = Board((BOARD_SIZE, BOARD_SIZE))
    mechanics = [mechanic_fn] if mechanic_fn else []
    game     = BaseGame(board, ai_players={PLAYER_1: agent1, PLAYER_2: agent2},
                        mechanics=mechanics)

    turn_count = 0
    while True:
        if turn_count > MAX_TURNS:
            return None, False   # Safety: didn't finish in time

        state = game.get_state()
        moves = game.possible_moves(state)

        if not moves:
            return None, True   # Draw — board full

        agent = game.ai_players[game.current_player]
        move  = agent.get_action(game, state)

        if not game.validate_move(move):
            return None, False  # Invalid move = broken mechanic

        game.perform_move(move)

        if game.game_finished():
            winner = game.get_winner()
            return winner, True

        game.current_player = game.next_player()
        game.turn           = game.turn_counter()
        turn_count += 1


def _run_game_safe(mechanic_fn, agent1, agent2) -> tuple:
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
        return run_single_game(mechanic_fn, agent1, agent2)
    except _GameTimeout:
        return None, False   # Timed out — treat as incomplete game
    except Exception:
        return None, False   # Any other crash — treat as incomplete
    finally:
        signal.alarm(0)                                  # Cancel pending alarm
        signal.signal(signal.SIGALRM, old_handler)       # Restore previous handler


def measure_playability_and_balance(mechanic_fn=None) -> tuple:
    """
    Run N games with two random agents.

    Returns:
        playability : fraction of games that completed normally (0.0 - 1.0)
        balance_gap : |P1_wins - P2_wins| / completed_games (0.0 = perfect balance)
    """
    completed = 0
    p1_wins   = 0
    p2_wins   = 0

    for _ in range(N_GAMES_BALANCE):
        winner, ok = _run_game_safe(mechanic_fn, RandomAgent(), RandomAgent())
        if not ok:
            # MIN_PLAYABILITY = 1.0 means any single failure guarantees discard.
            # Stop immediately instead of burning N_GAMES_BALANCE × GAME_TIMEOUT seconds.
            return 0.0, 1.0
        completed += 1
        if winner == PLAYER_1:
            p1_wins += 1
        elif winner == PLAYER_2:
            p2_wins += 1

    playability = completed / N_GAMES_BALANCE
    balance_gap = abs(p1_wins - p2_wins) / completed
    return playability, balance_gap


def measure_depth(mechanic_fn=None) -> float:
    """
    Run N games: GreedyAgent vs RandomAgent.
    A higher greedy win rate suggests the game rewards better decisions
    (i.e. more strategic depth).

    Returns:
        depth_proxy : greedy win rate (0.0 - 1.0)
    """
    greedy_wins = 0
    completed   = 0

    for _ in range(N_GAMES_DEPTH):
        # Greedy plays as PLAYER_1
        winner, ok = _run_game_safe(mechanic_fn, GreedyAgent(), RandomAgent())
        if ok:
            completed += 1
            if winner == PLAYER_1:
                greedy_wins += 1

    if completed == 0:
        return 0.0

    return greedy_wins / completed


def playtest(mechanic: dict) -> dict:
    """
    Full playtest of a mechanic. Runs automated games and returns scores.

    Args:
        mechanic : dict from proposal_module (must have 'python_code')

    Returns:
        scores dict with keys:
            playability  (higher is better, target >= 0.8)
            balance_gap  (lower is better, target <= 0.4)
            depth        (higher is better)
            aggregate    (combined score for ranking)
    """
    name = mechanic.get("mechanic_name", "unknown")
    code = mechanic.get("python_code", "")

    print(f"[Playtest] Running games for '{name}'...")

    mechanic_fn = load_mechanic_fn(code)

    playability, balance_gap = measure_playability_and_balance(mechanic_fn)
    depth                    = measure_depth(mechanic_fn)

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
