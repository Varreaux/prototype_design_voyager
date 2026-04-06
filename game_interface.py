"""
game_interface.py
=================
DesignVoyager — Game Interface Contract

Any game plugged into DesignVoyager must implement GameInterface.
The playtester, main loop, and compile checker call only these
methods — they know nothing about boards, cards, or any
game-specific data structure.
"""

from abc import ABC, abstractmethod


class GameAgent(ABC):
    """
    Abstract agent compatible with any GameInterface.
    Each game type provides its own RandomAgent and GreedyAgent
    that implement this.
    """

    @abstractmethod
    def choose_move(self, game: 'GameInterface', state: dict, moves: list):
        """
        Return one move chosen from the provided list of valid moves.
        The move format (string, int, etc.) is game-specific.
        """
        ...


class GameInterface(ABC):
    """
    Abstract contract for a base game in DesignVoyager.

    Implementing this interface is the only requirement for a game
    to be fully supported by the proposal, playtesting, and
    verification pipeline.
    """

    # ── Description (for LLM) ─────────────────────────────────────────────────

    @abstractmethod
    def get_skeleton_description(self) -> str:
        """Plain-English description of the game for the LLM prompt."""
        ...

    @abstractmethod
    def get_state_description(self) -> str:
        """
        Description of the state dict format injected into the system prompt
        so the LLM knows exactly which keys are available when writing mechanics.
        """
        ...

    # ── State ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def get_state(self) -> dict:
        """Return the current game state as a plain dict."""
        ...

    @abstractmethod
    def get_dummy_state(self) -> dict:
        """
        Return a minimal but realistic state dict for compile-checking
        mechanic code without running a full game.
        Must have the same top-level keys as a real game state.
        """
        ...

    # ── Moves ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def possible_moves(self, state: dict) -> list:
        """Return all valid moves for the current player."""
        ...

    @abstractmethod
    def is_valid_move(self, move) -> bool:
        """Return True if the move is legal right now."""
        ...

    @abstractmethod
    def perform_move(self, move) -> None:
        """
        Apply the move and any active mechanics to the game state.
        Modifies the game in-place.
        """
        ...

    @abstractmethod
    def next_state(self, state: dict, move) -> tuple:
        """
        Simulate a move without modifying the real game.

        Returns:
            (new_state: dict, game_ended: bool, agent_won: bool)
            new_state has current_player still set to the acting player
            (caller is responsible for flipping to the next player).
        """
        ...

    # ── Terminal conditions ────────────────────────────────────────────────────

    @abstractmethod
    def game_finished(self) -> bool:
        """Return True if the game has ended (win or draw)."""
        ...

    @abstractmethod
    def get_winner(self):
        """Return the winning player ID (int), or None for draw/unfinished."""
        ...

    # ── Turn management ───────────────────────────────────────────────────────

    @abstractmethod
    def get_current_agent(self) -> GameAgent:
        """Return the GameAgent for the current player."""
        ...

    @abstractmethod
    def advance_turn(self) -> None:
        """Advance to the next player's turn."""
        ...

    # ── Agent factories ───────────────────────────────────────────────────────

    @classmethod
    @abstractmethod
    def make_random_agent(cls) -> GameAgent:
        """Return a new random-play agent for this game type."""
        ...

    @classmethod
    @abstractmethod
    def make_greedy_agent(cls) -> GameAgent:
        """Return a new greedy-play agent for this game type."""
        ...

    @classmethod
    def make_mcts_agent(cls, simulations=40, exploration=1.4,
                        rollout_depth=16) -> 'GameAgent':
        """Return an MCTS agent. Works for any game type."""
        from mcts_agent import MCTSAgent
        return MCTSAgent(simulations=simulations, exploration=exploration,
                         rollout_depth=rollout_depth)

    # ── Instance factory ──────────────────────────────────────────────────────

    @classmethod
    @abstractmethod
    def create(cls, mechanic_fn=None, agent1: 'GameAgent' = None,
               agent2: 'GameAgent' = None) -> 'GameInterface':
        """
        Factory: return a fresh game instance ready to play.

        Args:
            mechanic_fn : optional callable applied as a mechanic each turn
            agent1      : agent for player 1 (default: make_random_agent())
            agent2      : agent for player 2 (default: make_random_agent())
        """
        ...
