"""
boardwalk.py
============
A bundled implementation of the Boardwalk framework.
Based on: Becker et al., "Boardwalk: Towards a Framework for Creating Board
Games with LLMs", arXiv:2508.16447, 2025.
https://github.com/LabCRAIG/boardwalk

We bundle this directly so no network install is required.
"""

import copy
import numpy as np


# -------------------------------------------------------
# Move helper functions
# -------------------------------------------------------

def is_placement(move: str) -> bool:
    """Returns True if the move is a placement action (e.g. 'X 2,3')."""
    try:
        parts = move.strip().split()
        if len(parts) != 2:
            return False
        piece = parts[0]
        coords = parts[1].split(',')
        return len(piece) == 1 and len(coords) == 2 and all(c.lstrip('-').isdigit() for c in coords)
    except Exception:
        return False


def is_movement(move: str) -> bool:
    """Returns True if the move is a movement action (e.g. '2,3 4,5')."""
    try:
        parts = move.strip().split()
        if len(parts) != 2:
            return False
        for part in parts:
            coords = part.split(',')
            if len(coords) != 2 or not all(c.lstrip('-').isdigit() for c in coords):
                return False
        return True
    except Exception:
        return False


def get_move_elements(move: str):
    """
    Parses a move string into its components.
    - Placement 'X 2,3'  -> ('X', (2, 3))
    - Movement  '2,3 4,5' -> ((2, 3), (4, 5))
    """
    parts = move.strip().split()
    if is_placement(move):
        piece = parts[0]
        coords = tuple(int(c) for c in parts[1].split(','))
        return (piece, coords)
    else:
        origin = tuple(int(c) for c in parts[0].split(','))
        dest   = tuple(int(c) for c in parts[1].split(','))
        return (origin, dest)


# -------------------------------------------------------
# Board class
# -------------------------------------------------------

class Board:
    """
    Represents the game board — a 2D grid of single characters.

    Reserved characters:
      '_'  BLANK space (a piece can be placed here)
      ' '  NULL  space (no piece can ever go here)
    """

    BLANK = '_'
    NULL  = ' '

    def __init__(self, shape: tuple, layout: str = None):
        """
        Args:
            shape  : (height, width) tuple
            layout : optional string defining the initial board layout.
                     Lines separated by '\\n', each character is one cell.
                     If omitted, the board starts fully blank.
        """
        self.height, self.width = shape
        if layout is not None:
            rows = layout.split('\n')
            self.layout = np.array([[c for c in row] for row in rows], dtype='<U1')
        else:
            self.layout = np.full((self.height, self.width), self.BLANK, dtype='<U1')

    # Allow board[r, c] read access
    def __getitem__(self, key):
        return self.layout[key]

    def __str__(self):
        rows = []
        for row in self.layout:
            rows.append(' '.join(row))
        return '\n'.join(rows)

    def place_piece(self, move: str):
        """Place a piece on the board. Move format: 'X row,col'"""
        piece, (row, col) = get_move_elements(move)
        self.layout[row, col] = piece

    def move_piece(self, move: str):
        """Move a piece from origin to destination. Move format: 'r1,c1 r2,c2'"""
        (r1, c1), (r2, c2) = get_move_elements(move)
        piece = self.layout[r1, c1]
        self.layout[r2, c2] = piece
        self.layout[r1, c1] = self.BLANK


# -------------------------------------------------------
# AIPlayer base class
# -------------------------------------------------------

class AIPlayer:
    """Base class for AI agents. Override get_action in your implementation."""

    def get_action(self, game, state: dict) -> str:
        raise NotImplementedError("AIPlayer subclasses must implement get_action()")


# -------------------------------------------------------
# Game class
# -------------------------------------------------------

class Game:
    """
    Base class for all Boardwalk games.

    To define a game, create a subclass and implement:
      - validate_move(move)  : is this move legal?
      - game_finished()      : has the game ended?
      - get_winner()         : who won? (return None for draw)
      - next_player()        : whose turn is it next?
      - possible_moves(state): list of all legal moves (required for AI)

    Optionally override:
      - perform_move(move)   : how does a move change the state?
      - get_state()          : what extra info goes in the state dict?
      - prompt_current_player(): how to ask a human for their move
      - finish_message(winner): what to print at the end
    """

    def __init__(self, board: Board, ai_players: dict = None):
        self.board          = board
        self.turn           = 1
        self.current_player = self.initial_player()
        self.ai_players     = ai_players or {}

    # ---- Final methods (do not override) ----

    def game_loop(self):
        """Run the game until completion. Returns the winner."""
        while True:
            print(self.board)
            valid = False
            while not valid:
                if self.current_player in self.ai_players:
                    agent = self.ai_players[self.current_player]
                    move  = agent.get_action(self, self.get_state())
                else:
                    move = self.prompt_current_player()
                valid = self.validate_move(move)
                if not valid:
                    print("Invalid move. Try again.")
            self.perform_move(move)
            if self.game_finished():
                print(f'\n{self.board}')
                winner = self.get_winner()
                self.finish_message(winner)
                return winner
            self.current_player = self.next_player()
            self.turn           = self.turn_counter()

    def next_state(self, state: dict, move: str):
        """
        Simulate a move without modifying the real game.
        Returns (new_state, game_ended, agent_won).
        """
        # Deep-copy all state into a temporary game
        saved_layout  = self.board.layout.copy()
        saved_turn    = self.turn
        saved_player  = self.current_player

        # Apply extra state variables
        extra_keys = {k: v for k, v in state.items()
                      if k not in ('board', 'turn', 'current_player')}
        saved_extra = {k: copy.deepcopy(getattr(self, k, None)) for k in extra_keys}

        # Restore incoming state
        self.board.layout   = state['board'].copy()
        self.turn           = state['turn']
        self.current_player = state['current_player']
        for k, v in extra_keys.items():
            setattr(self, k, copy.deepcopy(v))

        acting_player = self.current_player
        self.perform_move(move)
        ended  = self.game_finished()
        winner = self.get_winner() if ended else None
        new_state = self.get_state()

        # Restore real game state
        self.board.layout   = saved_layout
        self.turn           = saved_turn
        self.current_player = saved_player
        for k, v in saved_extra.items():
            setattr(self, k, v)

        agent_won = (winner == acting_player)
        return new_state, ended, agent_won

    # ---- Optionally overridable ----

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def initial_player(self) -> int:
        return 0

    def prompt_current_player(self) -> str:
        return input(f"Player {self.current_player}, enter your move: ")

    def perform_move(self, move: str):
        """Default: just place or move the piece on the board."""
        if is_placement(move):
            self.board.place_piece(move)
        elif is_movement(move):
            self.board.move_piece(move)

    def finish_message(self, winner):
        if winner is None:
            print("It's a draw!")
        else:
            print(f"Player {winner} wins!")

    def turn_counter(self) -> int:
        return self.turn + 1

    def get_state(self) -> dict:
        return {
            'board':          self.board.layout.copy(),
            'current_player': self.current_player,
            'turn':           self.turn,
        }

    # ---- Must be overridden ----

    def validate_move(self, move: str) -> bool:
        """Basic check: are the coordinates on the board?"""
        try:
            if is_placement(move):
                _, (r, c) = get_move_elements(move)
            elif is_movement(move):
                (r, c), _ = get_move_elements(move)
            else:
                return False
            return 0 <= r < self.board.height and 0 <= c < self.board.width
        except Exception:
            return False

    def game_finished(self) -> bool:
        raise NotImplementedError

    def get_winner(self):
        raise NotImplementedError

    def next_player(self) -> int:
        raise NotImplementedError

    def possible_moves(self, state: dict) -> list:
        raise NotImplementedError
