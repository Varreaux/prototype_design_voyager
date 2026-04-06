"""
card_game.py
============
DesignVoyager — Card Game

A simple two-player card game that implements GameInterface.
Exists purely to demonstrate that the DesignVoyager pipeline
is NOT tied to the board game — any game type can plug in.

Rules:
  - Each player starts with a hand of HAND_SIZE cards (values 1..MAX_CARD).
  - Players alternate. On your turn you play one card from your hand
    (move = index into your hand list), adding its value to your score.
  - First player to reach or exceed TARGET_SCORE wins.
  - If both hands empty with no winner it's a draw.
  - Mechanics receive and return the state dict, same as the board game.

State dict:
  {
    'hands'          : {1: [list of int card values], 2: [list of int card values]},
    'scores'         : {1: int, 2: int},
    'current_player' : int (1 or 2),
    'turn'           : int,
    'last_played'    : int or None  (value of the card just played)
  }

Move format: int index into the current player's hand (0-based),
             or -1 as a "pass" when the hand is empty.
"""

import copy
import random

from game_interface import GameInterface, GameAgent


# ── Constants ─────────────────────────────────────────────────────────────────

PLAYER_1     = 1
PLAYER_2     = 2
HAND_SIZE    = 8       # cards dealt to each player at game start
MAX_CARD     = 10      # card values drawn from range 1..MAX_CARD (inclusive)
TARGET_SCORE = 45      # first to reach this wins


class CardGame(GameInterface):
    """
    Simple card game. No boardwalk dependency — uses GameInterface only.
    """

    def __init__(self, state: dict = None, ai_players: dict = None,
                 mechanics: list = None):
        """
        Args:
            state      : full state dict (default: fresh deal)
            ai_players : {player_int: GameAgent}
            mechanics  : list of callables applied to state after each move
        """
        self.state      = state or _fresh_state()
        self.ai_players = ai_players or {}
        self.mechanics  = mechanics or []

    # ── GameInterface: descriptions ───────────────────────────────────────────

    def get_skeleton_description(self) -> str:
        return (
            f"Two-player card game. Each player starts with {HAND_SIZE} cards "
            f"drawn from values 1 to {MAX_CARD}. Players alternate playing one "
            f"card from their hand; the card's value is added to their score. "
            f"First player to reach {TARGET_SCORE} or more wins. "
            f"If both hands are exhausted without a winner it's a draw. "
            f"Mechanics can modify scores or hands after each play."
        )

    def get_state_description(self) -> str:
        return (
            "The game uses a state dictionary with these keys:\n"
            "  - 'hands'          : dict {1: [list of int card values], "
            "2: [list of int card values]}\n"
            "  - 'scores'         : dict {1: int, 2: int}\n"
            "  - 'current_player' : integer (1 or 2)\n"
            "  - 'turn'           : integer turn count\n"
            "  - 'last_played'    : int value of last card played, or None\n"
            "  - 'extra_turn'     : boolean, default False. Set to True to give the current "
            "player an extra turn (they go again immediately instead of the opponent).\n"
            "  - 'custom_state'   : dict, default {}. Use this to store anything you need to "
            "remember between turns — scores, token counts, cooldowns, flags, etc. "
            "Example: game_state['custom_state']['p1_tokens'] = 3. "
            "This dict persists across every turn of the game.\n"
            "IMPORTANT: Only use the keys listed above. Do NOT assume any other keys exist.\n"
            f"Win condition: first player to reach {TARGET_SCORE} points wins."
        )

    # ── GameInterface: state ──────────────────────────────────────────────────

    def get_state(self) -> dict:
        return copy.deepcopy(self.state)

    def get_dummy_state(self) -> dict:
        """Minimal realistic state for compile-checking mechanics."""
        return {
            'hands':          {1: [3, 6, 7], 2: [2, 5, 9]},
            'scores':         {1: 18, 2: 15},
            'current_player': PLAYER_1,
            'turn':           6,
            'last_played':    7,
            'extra_turn':     False,
            'custom_state':   {},
        }

    # ── GameInterface: moves ──────────────────────────────────────────────────

    def possible_moves(self, state: dict) -> list:
        """
        Returns indices (0-based) into the current player's hand.
        Returns [-1] (pass) when the hand is empty so the game loop
        never sees an empty move list.
        """
        hand = state['hands'][state['current_player']]
        if not hand:
            return [-1]
        return list(range(len(hand)))

    def is_valid_move(self, move) -> bool:
        """True if move is a valid hand index or the pass sentinel (-1)."""
        hand = self.state['hands'][self.state['current_player']]
        if not hand:
            return move == -1
        try:
            idx = int(move)
        except (TypeError, ValueError):
            return False
        return 0 <= idx < len(hand)

    def perform_move(self, move) -> None:
        """
        Play the card at index `move`, add to score, apply mechanics.
        Modifies self.state in-place.
        """
        player = self.state['current_player']
        hand   = self.state['hands'][player]

        if move == -1 or not hand:
            # Pass turn — no card played
            self.state['last_played'] = None
        else:
            card = hand.pop(int(move))
            self.state['scores'][player] += card
            self.state['last_played'] = card

        # Snapshot state after raw move but before mechanics (for replay diffs)
        self._state_before_mechanics = copy.deepcopy(self.state)

        # Apply mechanics (each receives and returns the full state dict)
        for mechanic_fn in self.mechanics:
            try:
                result = mechanic_fn(self.state)
                if isinstance(result, dict):
                    self._sync_from_state(result)
            except Exception as e:
                print(f"  [Mechanic] '{mechanic_fn.__name__}' crashed: {e}")

    def _sync_from_state(self, new_state: dict) -> None:
        """Write a mechanic-modified state dict back into self.state."""
        self.state.update(new_state)

    def next_state(self, state: dict, move) -> tuple:
        """
        Simulate a move without modifying the real game.
        Returns (new_state, game_ended, agent_won).
        """
        saved_state = copy.deepcopy(self.state)
        acting_player = state['current_player']

        # Temporarily apply the provided state
        self.state = copy.deepcopy(state)
        self.perform_move(move)

        ended     = self.game_finished()
        winner    = self.get_winner() if ended else None
        new_state = self.get_state()

        # Restore
        self.state = saved_state

        agent_won = (winner == acting_player)
        return new_state, ended, agent_won

    # ── GameInterface: terminal conditions ───────────────────────────────────

    def game_finished(self) -> bool:
        if self.get_winner() is not None:
            return True
        # Draw: both hands empty
        h1 = self.state['hands'][PLAYER_1]
        h2 = self.state['hands'][PLAYER_2]
        return len(h1) == 0 and len(h2) == 0

    def get_winner(self):
        """
        Returns player int if someone has reached TARGET_SCORE, else None.
        If both players are simultaneously at/above target, higher score wins;
        tie goes to neither (returns None).
        """
        s1 = self.state['scores'][PLAYER_1]
        s2 = self.state['scores'][PLAYER_2]
        p1_done = s1 >= TARGET_SCORE
        p2_done = s2 >= TARGET_SCORE

        if p1_done and p2_done:
            if s1 > s2:
                return PLAYER_1
            elif s2 > s1:
                return PLAYER_2
            return None  # exact tie
        if p1_done:
            return PLAYER_1
        if p2_done:
            return PLAYER_2
        return None

    # ── GameInterface: turn management ────────────────────────────────────────

    def get_current_agent(self) -> GameAgent:
        return self.ai_players[self.state['current_player']]

    def advance_turn(self) -> None:
        if self.state.get('extra_turn', False):
            self.state['extra_turn'] = False  # consume the flag, player stays the same
        else:
            cp = self.state['current_player']
            self.state['current_player'] = PLAYER_2 if cp == PLAYER_1 else PLAYER_1
        self.state['turn'] += 1

    # ── GameInterface: agent factories ────────────────────────────────────────

    @classmethod
    def make_random_agent(cls) -> 'CardRandomAgent':
        return CardRandomAgent()

    @classmethod
    def make_greedy_agent(cls) -> 'CardGreedyAgent':
        return CardGreedyAgent()

    # ── GameInterface: instance factory ──────────────────────────────────────

    @classmethod
    def create(cls, mechanic_fn=None, agent1=None,
               agent2=None) -> 'CardGame':
        """Factory: fresh game with optional mechanic and agents."""
        agent1    = agent1 or cls.make_random_agent()
        agent2    = agent2 or cls.make_random_agent()
        mechanics = [mechanic_fn] if mechanic_fn else []
        return cls(state=_fresh_state(),
                   ai_players={PLAYER_1: agent1, PLAYER_2: agent2},
                   mechanics=mechanics)


# ── Helper ─────────────────────────────────────────────────────────────────────

def _fresh_state() -> dict:
    """Deal fresh hands and reset scores."""
    return {
        'hands': {
            PLAYER_1: [random.randint(1, MAX_CARD) for _ in range(HAND_SIZE)],
            PLAYER_2: [random.randint(1, MAX_CARD) for _ in range(HAND_SIZE)],
        },
        'scores':         {PLAYER_1: 0, PLAYER_2: 0},
        'current_player': PLAYER_1,
        'turn':           0,
        'last_played':    None,
        'extra_turn':     False,
        'custom_state':   {},
    }


# ── Agents ─────────────────────────────────────────────────────────────────────

class CardRandomAgent(GameAgent):
    """Picks a random card index."""

    def choose_move(self, game: CardGame, state: dict, moves: list):
        return random.choice(moves)


class CardGreedyAgent(GameAgent):
    """
    Plays the highest-value card available (maximises own score gain).
    If hand is empty, passes (-1).
    """

    def choose_move(self, game: CardGame, state: dict, moves: list):
        if moves == [-1]:
            return -1
        player = state['current_player']
        hand   = state['hands'][player]
        # Pick index of the highest card
        return max(moves, key=lambda idx: hand[idx])


# ── Module-level helpers ───────────────────────────────────────────────────────

def get_skeleton_description() -> str:
    """Backwards-compat module-level helper."""
    return CardGame().get_skeleton_description()


if __name__ == "__main__":
    # Quick sanity check: two random agents play a game
    game = CardGame.create()
    print("Initial state:")
    s = game.get_state()
    print(f"  P1 hand: {s['hands'][1]}   P2 hand: {s['hands'][2]}")

    for _ in range(50):   # safety cap
        state = game.get_state()
        if game.game_finished():
            break
        moves  = game.possible_moves(state)
        agent  = game.get_current_agent()
        move   = agent.choose_move(game, state, moves)
        game.perform_move(move)
        game.advance_turn()

    winner = game.get_winner()
    s      = game.get_state()
    print(f"Final scores: P1={s['scores'][1]}  P2={s['scores'][2]}")
    print(f"Winner: Player {winner}" if winner else "Draw!")
