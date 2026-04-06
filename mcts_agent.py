"""
mcts_agent.py
=============
DesignVoyager -- Monte Carlo Tree Search Agent

Game-agnostic MCTS agent that implements GameAgent. Works with any
game that provides next_state() and possible_moves() via GameInterface.

Based on Cody Jiang's reference implementation, adapted to use the
GameAgent interface (choose_move) instead of AIPlayer (get_action).

Default parameters: simulations=40, exploration=1.4, rollout_depth=16.
"""

import copy
import math
import random

from game_interface import GameAgent


class _Node:
    """A node in the MCTS search tree."""

    __slots__ = ('state', 'parent', 'move', 'children',
                 'visits', 'value', 'untried_moves', 'terminal')

    def __init__(self, state, parent=None, move=None, untried_moves=None):
        self.state         = state
        self.parent        = parent
        self.move          = move
        self.children      = []
        self.visits        = 0
        self.value         = 0.0
        self.untried_moves = untried_moves
        self.terminal      = False


def _flip_player(state: dict) -> dict:
    """
    Return a shallow copy of state with current_player flipped
    and turn incremented. Used after next_state() to set up the
    child node for the next player's perspective.
    """
    s = dict(state)
    # Copy mutable values that next_state might share by reference
    cp = s['current_player']
    s['current_player'] = 2 if cp == 1 else 1
    s['turn'] = s.get('turn', 0) + 1
    return s


class MCTSAgent(GameAgent):
    """
    Monte Carlo Tree Search agent with random rollouts.

    Args:
        simulations   : number of MCTS iterations per move decision
        exploration   : UCB1 exploration constant (higher = more exploration)
        rollout_depth : maximum moves per random rollout before returning 0.5
    """

    def __init__(self, simulations: int = 40, exploration: float = 1.4,
                 rollout_depth: int = 16):
        self.simulations   = simulations
        self.exploration    = exploration
        self.rollout_depth = rollout_depth

    def choose_move(self, game, state: dict, moves: list):
        """Pick the best move using MCTS. Returns one item from moves."""
        if len(moves) == 1:
            return moves[0]

        root_player = state['current_player']
        root = _Node(
            state=copy.deepcopy(state),
            untried_moves=list(moves),
        )

        for _ in range(self.simulations):
            # ── Selection ────────────────────────────────────────────
            node = root
            while not node.untried_moves and node.children:
                node = self._select_child(node)

            # ── Expansion ────────────────────────────────────────────
            terminal_reward = None  # set only when expansion lands on a terminal node
            if node.untried_moves:
                move          = node.untried_moves.pop()
                acting_player = node.state['current_player']
                new_state, ended, agent_won = game.next_state(node.state, move)
                child_state = _flip_player(new_state)

                if ended:
                    child = _Node(state=child_state, parent=node, move=move,
                                  untried_moves=[])
                    child.terminal = True
                    # Reward is known — skip rollout entirely
                    if agent_won:
                        terminal_reward = 1.0 if acting_player == root_player else 0.0
                    else:
                        terminal_reward = 0.5  # draw
                else:
                    child_moves = game.possible_moves(child_state)
                    child = _Node(state=child_state, parent=node, move=move,
                                  untried_moves=child_moves)

                node.children.append(child)
                node = child

            # ── Simulation (rollout) ─────────────────────────────────
            if terminal_reward is not None:
                reward = terminal_reward
            else:
                reward = self._rollout(game, node.state, root_player)

            # ── Backpropagation ──────────────────────────────────────
            while node is not None:
                node.visits += 1
                node.value += reward
                node = node.parent

        # Pick the child with the most visits (most robust choice)
        best = max(root.children, key=lambda c: c.visits)
        return best.move

    def _select_child(self, node: _Node) -> _Node:
        """Select the child with the highest UCB1 score."""
        log_parent = math.log(max(node.visits, 1))
        C = self.exploration

        def ucb(child):
            if child.visits == 0:
                return float('inf')
            return (child.value / child.visits
                    + C * math.sqrt(log_parent / child.visits))

        return max(node.children, key=ucb)

    def _rollout(self, game, state: dict, root_player: int) -> float:
        """
        Random playout from the given state.
        Returns reward from root_player's perspective:
            1.0 = root player wins, 0.0 = root player loses, 0.5 = draw/timeout
        """
        rollout_state = copy.deepcopy(state)

        for _ in range(self.rollout_depth):
            moves = game.possible_moves(rollout_state)
            if not moves:
                return 0.5  # no moves, draw

            move = random.choice(moves)
            acting = rollout_state['current_player']
            new_state, ended, agent_won = game.next_state(rollout_state, move)

            if ended:
                if agent_won:
                    return 1.0 if acting == root_player else 0.0
                return 0.5  # draw

            rollout_state = _flip_player(new_state)

        return 0.5  # rollout depth exceeded


# ── Quick sanity check ───────────────────────────────────────────────────────

if __name__ == "__main__":
    from base_game import BaseGame
    from card_game import CardGame

    def run_test_game(game_class, label, sims=20):
        a1   = game_class.make_mcts_agent(simulations=sims)
        a2   = game_class.make_mcts_agent(simulations=sims)
        game = game_class.create(agent1=a1, agent2=a2)

        for _ in range(100):
            state = game.get_state()
            if game.game_finished():
                break
            moves = game.possible_moves(state)
            agent = game.get_current_agent()
            move  = agent.choose_move(game, state, moves)
            game.perform_move(move)
            game.advance_turn()

        winner = game.get_winner()
        turn   = game.get_state().get('turn', '?')
        print(f"{label}: winner=Player {winner}, turns={turn}")

    print("Testing MCTS agent...")
    run_test_game(BaseGame, "Board game", sims=20)
    run_test_game(CardGame, "Card game",  sims=20)
    print("Done.")
