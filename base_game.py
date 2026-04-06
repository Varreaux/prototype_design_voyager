"""
base_game.py
============
The starting game skeleton for DesignVoyager.

A simple two-player grid game: players take turns placing pieces,
first to get 4 in a row (horizontal, vertical, or diagonal) wins.
Think Connect-4 but on a flat 6x6 board without gravity.

This is the "blank canvas" that the Proposal Module adds mechanics to
over time.

Now implements GameInterface so the playtester and main loop can treat
it identically to any other game — no board-specific assumptions needed
outside this file.
"""

import copy
import random
import numpy as np
from boardwalk import Board, Game, AIPlayer, is_placement, get_move_elements
from game_interface import GameInterface, GameAgent


# Player identifiers
PLAYER_1 = 1   # uses piece 'X'
PLAYER_2 = 2   # uses piece 'O'
PIECES    = {PLAYER_1: 'X', PLAYER_2: 'O'}
WIN_LENGTH = 4
BOARD_SIZE = 6


class BaseGame(Game, GameInterface):
    """
    Simple 6x6 two-player placement game.
    Win condition: 4 pieces in a row (any direction).

    Inherits from both boardwalk.Game (game engine) and GameInterface
    (DesignVoyager's abstract contract) so the pipeline can treat it
    identically to any other plugged-in game.
    """

    def __init__(self, board: Board = None, ai_players: dict = None, mechanics: list = None):
        """
        Args:
            board      : optional pre-built Board (default: fresh 6x6 blank board)
            ai_players : optional dict of {player_int: AIPlayer/GameAgent}
            mechanics  : list of Python function objects to apply after each move
        """
        if board is None:
            board = Board((BOARD_SIZE, BOARD_SIZE))
        super().__init__(board, ai_players)
        self.mechanics           = mechanics or []   # Extra mechanics to apply each turn
        self.last_move           = None              # (row, col) of the most recent placement
        self._extra_turn_pending = False             # set by mechanic to grant extra turn
        self.custom_state        = {}               # persistent scratch space for mechanics

    def initial_player(self) -> int:
        return PLAYER_1

    def next_player(self) -> int:
        return PLAYER_2 if self.current_player == PLAYER_1 else PLAYER_1

    def validate_move(self, move: str) -> bool:
        """A move is valid if it's a placement on a blank square."""
        if not super().validate_move(move):
            return False
        if not is_placement(move):
            return False
        _, (r, c) = get_move_elements(move)
        return self.board[r, c] == '_'

    def perform_move(self, move: str):
        """Place the current player's piece, then apply any extra mechanics."""
        piece = PIECES[self.current_player]
        # Re-format move with the correct piece character
        _, (r, c) = get_move_elements(move)
        self.board.place_piece(f"{piece} {r},{c}")

        # Track the most recent placement so mechanics can reference it
        self.last_move = (r, c)

        # Snapshot state after raw move but before mechanics (for replay diffs)
        self._state_before_mechanics = self.get_state()

        # Apply any mechanics that have been added to this game
        state = self.get_state()
        for mechanic_fn in self.mechanics:
            try:
                state = mechanic_fn(state)
            except Exception as e:
                print(f"  [Mechanic] '{mechanic_fn.__name__}' crashed: {e}")
        # Sync board back from state (mechanics may have modified it)
        self.board.layout = state['board']
        # Respect extra-turn request from mechanics
        self._extra_turn_pending = bool(state.get('extra_turn', False))
        # Persist any custom data mechanics stored this turn
        self.custom_state = dict(state.get('custom_state', {}))

    def game_finished(self) -> bool:
        """Game ends if someone has won or the board is full."""
        if self.get_winner() is not None:
            return True
        return not np.any(self.board.layout == '_')

    def get_winner(self):
        """
        Check all lines of WIN_LENGTH for a matching sequence.
        Returns the winning player int, or None.
        """
        board = self.board.layout
        h, w  = board.shape

        for player, piece in PIECES.items():
            # Horizontal
            for r in range(h):
                for c in range(w - WIN_LENGTH + 1):
                    if all(board[r, c + i] == piece for i in range(WIN_LENGTH)):
                        return player
            # Vertical
            for r in range(h - WIN_LENGTH + 1):
                for c in range(w):
                    if all(board[r + i, c] == piece for i in range(WIN_LENGTH)):
                        return player
            # Diagonal down-right
            for r in range(h - WIN_LENGTH + 1):
                for c in range(w - WIN_LENGTH + 1):
                    if all(board[r + i, c + i] == piece for i in range(WIN_LENGTH)):
                        return player
            # Diagonal up-right
            for r in range(WIN_LENGTH - 1, h):
                for c in range(w - WIN_LENGTH + 1):
                    if all(board[r - i, c + i] == piece for i in range(WIN_LENGTH)):
                        return player
        return None

    def get_state(self) -> dict:
        """Return the standard state dict (board, player, turn, last_move, extra_turn, custom_state)."""
        state = super().get_state()
        state['last_move']    = self.last_move        # (row, col) or None
        state['extra_turn']   = False                 # mechanics set this True to grant another turn
        state['custom_state'] = dict(self.custom_state)  # persistent mechanic scratch space
        return state

    def possible_moves(self, state: dict) -> list:
        """Return all blank squares as valid placement moves."""
        board   = state['board']
        player  = state['current_player']
        piece   = PIECES[player]
        h, w    = board.shape
        moves   = []
        for r in range(h):
            for c in range(w):
                if board[r, c] == '_':
                    moves.append(f"{piece} {r},{c}")
        return moves

    def get_skeleton_description(self) -> str:
        """Returns a plain-English description of the current game for the LLM."""
        n_mechanics = len(self.mechanics)
        return (
            f"Two-player board game on a {BOARD_SIZE}x{BOARD_SIZE} grid. "
            f"Players (X and O) take turns placing their pieces on empty squares. "
            f"First player to get {WIN_LENGTH} pieces in a row "
            f"(horizontally, vertically, or diagonally) wins. "
            f"If the board fills up with no winner, it's a draw. "
            f"Current number of active mechanics: {n_mechanics}."
        )

    def get_state_description(self) -> str:
        """Description of the state dict format, injected into the LLM system prompt."""
        return (
            "The game uses a state dictionary with these keys:\n"
            "  - 'board'          : a 2D numpy array of single characters "
            "('_' = blank, 'X' = player 1, 'O' = player 2)\n"
            "  - 'current_player' : integer (1 or 2)\n"
            "  - 'turn'           : integer turn count\n"
            "  - 'last_move'      : tuple (row, col) of the most recent piece placement, "
            "or None on the very first call\n"
            "  - 'extra_turn'     : boolean, default False. Set to True to give the current "
            "player an extra turn (they go again immediately instead of the opponent).\n"
            "  - 'custom_state'   : dict, default {}. Use this to store anything you need to "
            "remember between turns — scores, token counts, cooldowns, flags, etc. "
            "Example: game_state['custom_state']['p1_tokens'] = 3. "
            "This dict persists across every turn of the game.\n"
            "Note: numpy is imported as 'np' inside mechanic functions.\n"
            "IMPORTANT: Only use the keys listed above. Do NOT assume any other keys exist."
        )

    def get_dummy_state(self) -> dict:
        """Minimal realistic state for compile-checking mechanics without a full game."""
        state = {
            'board':          np.full((BOARD_SIZE, BOARD_SIZE), '_', dtype='<U1'),
            'current_player': PLAYER_1,
            'turn':           1,
            'last_move':      (0, 0),
            'extra_turn':     False,
            'custom_state':   {},
        }
        state['board'][0, 0] = 'X'
        state['board'][1, 1] = 'O'
        return state

    # ── GameInterface turn management ─────────────────────────────────────────

    def is_valid_move(self, move) -> bool:
        """GameInterface wrapper around boardwalk's validate_move."""
        return self.validate_move(move)

    def get_current_agent(self) -> GameAgent:
        """Return the agent registered for the current player."""
        return self.ai_players[self.current_player]

    def advance_turn(self) -> None:
        """Advance to the next player and increment the turn counter.
        If a mechanic set extra_turn=True this turn, the same player goes again."""
        if self._extra_turn_pending:
            self._extra_turn_pending = False  # consume the flag, player stays the same
        else:
            self.current_player = self.next_player()
        self.turn = self.turn_counter()

    # ── Agent factories (classmethods) ────────────────────────────────────────

    @classmethod
    def make_random_agent(cls) -> 'RandomAgent':
        return RandomAgent()

    @classmethod
    def make_greedy_agent(cls) -> 'GreedyAgent':
        return GreedyAgent()

    # ── Instance factory ──────────────────────────────────────────────────────

    @classmethod
    def create(cls, mechanic_fn=None, agent1=None, agent2=None) -> 'BaseGame':
        """
        Factory: create a fresh board game instance.
        The mechanic (if any) is wrapped in a list as boardwalk expects.
        """
        agent1    = agent1 or cls.make_random_agent()
        agent2    = agent2 or cls.make_random_agent()
        mechanics = [mechanic_fn] if mechanic_fn else []
        board     = Board((BOARD_SIZE, BOARD_SIZE))
        return cls(board, ai_players={PLAYER_1: agent1, PLAYER_2: agent2},
                   mechanics=mechanics)


# -------------------------------------------------------
# Simple AI agents (no API needed — pure Python)
# -------------------------------------------------------

class RandomAgent(AIPlayer, GameAgent):
    """Picks a random valid move. Used for playtesting."""

    def get_action(self, game, state: dict) -> str:
        moves = game.possible_moves(state)
        return random.choice(moves) if moves else ""

    def choose_move(self, game, state: dict, moves: list):
        # Board game agents delegate choose_move to get_action.
        # The 'moves' list is ignored here; get_action recomputes it
        # internally, but the result is identical since possible_moves
        # is deterministic given the same state.
        return self.get_action(game, state)


class GreedyAgent(AIPlayer, GameAgent):
    """
    Slightly smarter agent: wins immediately if it can,
    blocks opponent's immediate win if possible,
    otherwise plays randomly.
    Used to measure strategic depth during playtesting.
    """

    def get_action(self, game, state: dict) -> str:
        moves = game.possible_moves(state)
        if not moves:
            return ""

        # Try to win immediately
        for move in moves:
            new_state, ended, won = game.next_state(state, move)
            if ended and won:
                return move

        # Block opponent's immediate win
        opponent = PLAYER_2 if state['current_player'] == PLAYER_1 else PLAYER_1
        opp_piece = PIECES[opponent]
        fake_state = copy.deepcopy(state)
        fake_state['current_player'] = opponent
        opp_moves = game.possible_moves(fake_state)
        for move in opp_moves:
            # Re-map move to opponent's piece
            _, coords = get_move_elements(move)
            opp_move = f"{opp_piece} {coords[0]},{coords[1]}"
            new_state, ended, won = game.next_state(fake_state, opp_move)
            if ended and won:
                # Block it — play our piece there
                my_piece = PIECES[state['current_player']]
                return f"{my_piece} {coords[0]},{coords[1]}"

        return random.choice(moves)

    def choose_move(self, game, state: dict, moves: list):
        # Delegate to get_action which contains the board-specific win/block logic.
        return self.get_action(game, state)


def get_skeleton_description() -> str:
    """Module-level helper so main.py can import it directly (backwards compat)."""
    return BaseGame(Board((BOARD_SIZE, BOARD_SIZE))).get_skeleton_description()


if __name__ == "__main__":
    # Quick sanity check: two random agents play a game
    board = Board((BOARD_SIZE, BOARD_SIZE))
    game  = BaseGame(
        board,
        ai_players={PLAYER_1: RandomAgent(), PLAYER_2: RandomAgent()}
    )
    winner = game.game_loop()
    print(f"\nWinner: Player {winner}" if winner else "\nDraw!")
