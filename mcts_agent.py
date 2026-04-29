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
    Return a shallow copy of state advanced to the next decision point.

    Normally the next move belongs to the opposing player, so current_player
    is flipped. When the prior mechanic set extra_turn=True, the same player
    goes again (matching the real game's advance_turn semantics) and the
    flag is consumed so a single mechanic firing cannot chain extra turns
    forever inside the search tree.
    """
    s = dict(state)
    if s.get('extra_turn', False):
        s['extra_turn'] = False
    else:
        cp = s['current_player']
        s['current_player'] = 2 if cp == 1 else 1
    s['turn'] = s.get('turn', 0) + 1
    return s


def _extract_square_coords(move):
    """
    Extract (row, col) from a placement-style move like "X 2,3" or "O 0,5".
    Returns None for moves that don't encode a board square (e.g. card-game
    integer indices), so callers can detect and fall through.
    """
    if not isinstance(move, str):
        return None
    parts = move.split()
    if len(parts) != 2 or ',' not in parts[1]:
        return None
    try:
        r_str, c_str = parts[1].split(',')
        return (int(r_str), int(c_str))
    except (ValueError, AttributeError):
        return None


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
        opp_player  = 2 if root_player == 1 else 1

        # Tactical preamble (1-ply, symmetric).
        #
        # Step 1: take any immediate winning move for me. Vanilla MCTS at
        # low simulation budgets often fails to take obvious one-ply wins
        # because random rollouts may not actually take the win and the
        # winning move ends up looking no better than its neighbors.
        #
        # Step 2: block any immediate winning move for the opponent. This
        # MUST exist alongside step 1 -- otherwise the agent takes wins
        # but never defends, which destroys the playtest's balance phase
        # (player 1 sets up the first 3-in-a-row, player 2's MCTS rollouts
        # don't see the threat at low sim counts, player 1 wins every
        # game, balance_gap pegs at 1.0).
        #
        # next_state may mutate its input, so each call takes a deepcopy.
        for move in moves:
            _, ended, agent_won = game.next_state(copy.deepcopy(state), move)
            if ended and agent_won:
                return move

        # Build a hypothetical state where the opponent moves next, then
        # find every move that lets them win immediately. Those are the
        # threat squares we want to block.
        opp_state = copy.deepcopy(state)
        opp_state['current_player'] = opp_player
        try:
            opp_moves = game.possible_moves(opp_state)
        except Exception:
            opp_moves = []
        threat_coords = set()
        for opp_move in opp_moves:
            try:
                _, opp_ended, opp_won = game.next_state(
                    copy.deepcopy(opp_state), opp_move,
                )
            except Exception:
                continue
            if opp_ended and opp_won:
                coords = _extract_square_coords(opp_move)
                if coords is not None:
                    threat_coords.add(coords)

        # If the opponent has any one-ply winning square AND one of my
        # legal moves lands on the same square, play it. If multiple
        # threats exist (a fork), blocking one still leaves the others
        # but is still strictly better than ignoring them. If my legal
        # moves don't encode coords (card game), this falls through.
        if threat_coords:
            for my_move in moves:
                my_coords = _extract_square_coords(my_move)
                if my_coords is not None and my_coords in threat_coords:
                    return my_move

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


# ── Minimax (alpha-beta) agent ────────────────────────────────────────────────
#
# Iterative-deepening alpha-beta with a heuristic eval at depth-cut leaves.
# Designed for the card game: strong enough that the user can treat it as
# effectively unbeatable at depth 7+ (worst-case the agent solves the rest
# of the game when the search tree gets small enough).

import time

_INF = float('inf')


class _DeadlineExceeded(Exception):
    """Raised inside the search to bail out when the time budget is gone."""


class MinimaxAgent(GameAgent):
    """
    Iterative-deepening alpha-beta agent.

    Args:
        max_depth     : maximum search depth (plies). Each ply is one move.
        time_budget_s : soft wall-clock budget per move. Search starts at
                        depth 2 and increases by 2 each iteration; the
                        deepest fully-completed iteration's best move is
                        returned. If even depth=2 doesn't finish, the
                        first move is returned as a fallback.

    Heuristic eval at non-terminal leaves: score difference plus half the
    remaining hand-value difference. Wins/losses receive a large constant
    so they always dominate heuristic comparisons; faster wins are
    preferred over slower ones.

    Move ordering tries high-value cards first when maximizing and
    low-value cards first when minimizing — this is a good default for
    the card game and makes alpha-beta cut a lot more branches.
    """

    WIN_VALUE = 10_000

    def __init__(self, max_depth: int = 7, time_budget_s: float = 3.0):
        self.max_depth = max_depth
        self.time_budget_s = time_budget_s

    def choose_move(self, game, state, moves):
        if len(moves) == 1:
            return moves[0]

        root_player = state['current_player']
        deadline = time.time() + self.time_budget_s
        best_move = moves[0]

        # Iterative deepening: keep the deepest fully-completed search's move.
        for depth in range(2, self.max_depth + 1, 2):
            try:
                _, move = self._search(game, state, depth, -_INF, _INF,
                                       root_player, deadline)
                if move is not None:
                    best_move = move
            except _DeadlineExceeded:
                break

        return best_move

    def _search(self, game, state, depth, alpha, beta, root_player, deadline):
        if time.time() > deadline:
            raise _DeadlineExceeded()

        if depth == 0:
            return self._eval(state, root_player), None

        moves = game.possible_moves(state)
        if not moves:
            return self._eval(state, root_player), None

        is_max = (state['current_player'] == root_player)
        best_move = None
        ordered = self._order_moves(state, moves, prefer_high=is_max)

        if is_max:
            value = -_INF
            for move in ordered:
                child_value = self._evaluate_move(
                    game, state, move, depth, alpha, beta, root_player, deadline,
                )
                if child_value > value:
                    value = child_value
                    best_move = move
                alpha = max(alpha, value)
                if alpha >= beta:
                    break
            return value, best_move
        else:
            value = _INF
            for move in ordered:
                child_value = self._evaluate_move(
                    game, state, move, depth, alpha, beta, root_player, deadline,
                )
                if child_value < value:
                    value = child_value
                    best_move = move
                beta = min(beta, value)
                if alpha >= beta:
                    break
            return value, best_move

    def _evaluate_move(self, game, state, move, depth, alpha, beta,
                        root_player, deadline):
        new_state, ended, _ = game.next_state(state, move)
        if ended:
            winner = game.get_winner()
            if winner == root_player:
                # Prefer faster wins (higher score = better).
                return self.WIN_VALUE - state.get('turn', 0)
            elif winner is None:
                # Hand-empty draw (no one hit the target). Score diff still matters.
                return self._eval(new_state, root_player)
            else:
                return -self.WIN_VALUE + state.get('turn', 0)
        else:
            child_state = self._advance(new_state)
            value, _ = self._search(
                game, child_state, depth - 1, alpha, beta, root_player, deadline,
            )
            return value

    @staticmethod
    def _advance(state):
        """Mirror MCTS _flip_player: respect and consume extra_turn."""
        s = dict(state)
        if s.get('extra_turn', False):
            s['extra_turn'] = False
        else:
            cp = s['current_player']
            s['current_player'] = 2 if cp == 1 else 1
        s['turn'] = s.get('turn', 0) + 1
        return s

    @staticmethod
    def _eval(state, root_player):
        """Heuristic value of `state` from root_player's perspective."""
        scores = state.get('scores', {})
        hands = state.get('hands', {})
        opp = 2 if root_player == 1 else 1

        my_score = scores.get(root_player, 0)
        opp_score = scores.get(opp, 0)
        my_hand = list(hands.get(root_player, []))
        opp_hand = list(hands.get(opp, []))

        # Score is primary; remaining-hand value is partial credit because
        # you still get to play those cards.
        return (my_score - opp_score) + 0.5 * (sum(my_hand) - sum(opp_hand))

    @staticmethod
    def _order_moves(state, moves, prefer_high):
        """Sort card-game moves by hand value to improve alpha-beta cuts."""
        if not moves:
            return list(moves)
        if moves == [-1]:
            return [-1]
        player = state.get('current_player', 1)
        hand = state.get('hands', {}).get(player, [])
        scored = []
        for m in moves:
            if m == -1:
                scored.append((-_INF, m))
            elif isinstance(m, int) and 0 <= m < len(hand):
                scored.append((hand[m], m))
            else:
                # Unknown move shape (e.g. board-game string move).
                # Preserve original order for these.
                scored.append((0, m))
        scored.sort(key=lambda x: x[0], reverse=prefer_high)
        return [m for _, m in scored]


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
