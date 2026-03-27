"""
base_game.py
============
The starting game skeleton for DesignVoyager.

A simple two-player grid game: players take turns placing pieces,
first to get 4 in a row (horizontal, vertical, or diagonal) wins.
Think Connect-4 but on a flat 6x6 board without gravity.

This is the "blank canvas" that the Proposal Module adds mechanics to
over time.
"""

import copy
import random
import numpy as np
from boardwalk import Board, Game, AIPlayer, is_placement, get_move_elements


# Player identifiers
PLAYER_1 = 1   # uses piece 'X'
PLAYER_2 = 2   # uses piece 'O'
PIECES    = {PLAYER_1: 'X', PLAYER_2: 'O'}
WIN_LENGTH = 4
BOARD_SIZE = 6


class BaseGame(Game):
    """
    Simple 6x6 two-player placement game.
    Win condition: 4 pieces in a row (any direction).
    """

    def __init__(self, board: Board = None, ai_players: dict = None, mechanics: list = None):
        """
        Args:
            board      : optional pre-built Board (default: fresh 6x6 blank board)
            ai_players : optional dict of {player_int: AIPlayer}
            mechanics  : list of Python function objects to apply after each move
        """
        if board is None:
            board = Board((BOARD_SIZE, BOARD_SIZE))
        super().__init__(board, ai_players)
        self.mechanics = mechanics or []   # Extra mechanics to apply each turn

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

        # Apply any mechanics that have been added to this game
        state = self.get_state()
        for mechanic_fn in self.mechanics:
            try:
                state = mechanic_fn(state)
            except Exception:
                pass   # If a mechanic crashes, skip it gracefully
        # Sync board back from state (mechanics may have modified it)
        self.board.layout = state['board']

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
            # Diagonal ↘
            for r in range(h - WIN_LENGTH + 1):
                for c in range(w - WIN_LENGTH + 1):
                    if all(board[r + i, c + i] == piece for i in range(WIN_LENGTH)):
                        return player
            # Diagonal ↗
            for r in range(WIN_LENGTH - 1, h):
                for c in range(w - WIN_LENGTH + 1):
                    if all(board[r - i, c + i] == piece for i in range(WIN_LENGTH)):
                        return player
        return None

    def get_state(self) -> dict:
        """Return the standard state dict (board, player, turn)."""
        state = super().get_state()
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
        """Returns a plain-English description of the current game for GPT-4."""
        n_mechanics = len(self.mechanics)
        return (
            f"Two-player board game on a {BOARD_SIZE}x{BOARD_SIZE} grid. "
            f"Players (X and O) take turns placing their pieces on empty squares. "
            f"First player to get {WIN_LENGTH} pieces in a row "
            f"(horizontally, vertically, or diagonally) wins. "
            f"If the board fills up with no winner, it's a draw. "
            f"Current number of active mechanics: {n_mechanics}."
        )


# -------------------------------------------------------
# Simple AI agents (no OpenAI needed — pure Python)
# -------------------------------------------------------

class RandomAgent(AIPlayer):
    """Picks a random valid move. Used for playtesting."""

    def get_action(self, game, state: dict) -> str:
        moves = game.possible_moves(state)
        return random.choice(moves) if moves else ""


class GreedyAgent(AIPlayer):
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


def get_skeleton_description() -> str:
    """Module-level helper so main.py can import it directly."""
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
