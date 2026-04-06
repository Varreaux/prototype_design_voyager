# DesignVoyager Session Handoff

## IMPORTANT: Before doing any work, read through the key files in this project to build full context:
- `game_interface.py` (the abstract contracts)
- `base_game.py` (board game implementation)
- `card_game.py` (card game implementation)
- `proposal_module.py` (LLM mechanic proposal)
- `compile_check.py` (syntax + runtime validation)
- `playtest_module.py` (automated game testing)
- `mechanic_library.py` (persistent storage + retrieval)
- `verification_module.py` (accept/revise/discard logic)
- `curriculum.py` (3-stage progression system)
- `main.py` and `demo_main.py` (entry points with `--game` flag)

Read these before writing any code so you understand the full architecture.

## Where We Are

We just completed a 5-step generalization refactor so the pipeline supports multiple game types, not just the board game.

### What was done (all tested and working):

1. **game_interface.py** (new) -- Abstract base classes `GameInterface` and `GameAgent` that define the contract any game must implement
2. **base_game.py** (modified) -- Board game now inherits from `GameInterface`. `RandomAgent` and `GreedyAgent` implement `GameAgent`. Added methods: `get_state_description()`, `get_dummy_state()`, `is_valid_move()`, `get_current_agent()`, `advance_turn()`, `make_random_agent()`, `make_greedy_agent()`, `create()`
3. **card_game.py** (new) -- Simple two-player card game implementing `GameInterface` with no boardwalk dependency. Players have hands of numbered cards (1-10), play one per turn, first to 21 wins. Has `CardRandomAgent` and `CardGreedyAgent`
4. **Pipeline made game-agnostic:**
   - `compile_check.py` -- accepts `dummy_state` param, validates all state keys are preserved
   - `proposal_module.py` -- `_build_system_prompt(state_description)` generates game-specific LLM prompts, `propose_mechanic()` takes `state_description` param
   - `playtest_module.py` -- all functions accept `game_class` param, game loop uses `GameInterface` methods
   - `mechanic_library.py` -- added `clear()` method
5. **main.py and demo_main.py** -- both have `--game board|card` flag, `GAME_REGISTRY` dict, separate library files per game (`library.json` vs `library_card.json`)

### Commands to verify everything works:

```bash
python3 main.py --game board --iterations 3
python3 main.py --game card --iterations 3
```

Both have been run successfully. Board game: 3/3 accepted. Card game: 3/3 accepted.

## Known Issues to Fix

1. **Duplicate mechanic proposals** -- The LLM sometimes proposes the same mechanic multiple times in a row (e.g. `extra_turn_on_corner_placement` three times). Two fixes needed:
   - Add existing mechanic names to the proposal prompt so the LLM avoids repeats
   - Add deduplication check in `MechanicLibrary.add()`

2. **Library needs cleaning** -- `library.json` currently has duplicate entries from the repeated proposals. Run `rm library.json library_card.json` to start fresh, or use `MechanicLibrary("library.json").clear()`

## Next Up: Web UI Dashboard

We're planning a polished web dashboard (Flask app) with:
- Pipeline progress view (iterations, scores, verdicts) -- prettier version of what `demo_main.py` shows in the terminal
- Animated game replay -- watch bots play moves on the board/card game during playtesting
- Architecture: Flask/FastAPI backend with WebSocket streaming to browser frontend

## Next Priority: MCTS Agent for Playtesting

Replace the current greedy/random agents with MCTS (Monte Carlo Tree Search) agents. Reference implementation exists at https://github.com/Cody-Jiang-Zhihong/DesignVoyager (see `Prototype/mcts_agent.py`).

Key changes needed:
- Create `mcts_agent.py` implementing `GameAgent` (our interface, not just AIPlayer)
- Make it game-agnostic so it works with both board and card games via `GameInterface`
- Balance test: two equal-budget MCTS agents (replaces random-vs-random)
- Depth test: strong MCTS (50 sims) vs weak MCTS (10 sims), alternating seats (replaces greedy-vs-random)
- Reduce game counts (60 balance, 40 depth) since MCTS games are slower
- Consider subprocess-based timeouts instead of signal.alarm for portability

The reference repo uses `simulations=40`, `exploration=1.4`, `rollout_depth=16`. These may need tuning per game type.

## Backburner Tasks

- Stateless baseline (random mechanic generator, no library/retrieval)
- Evolutionary search baseline
- Look into retrieval sensitivity (professor feedback)

## Morgan's Preferences

- No em dashes or double dashes. Use commas or rephrase instead.
- Prefers plain-language explanations (NYU student, new to AI coding tools)
- Owns the Proposal Module in the team project
- Class: AI in Games (Julian Togelius, NYU)
