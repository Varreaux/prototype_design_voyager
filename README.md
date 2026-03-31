# DesignVoyager

An autonomous game mechanic designer. DesignVoyager uses a large language model to propose new board game mechanics as Python code, then evaluates them through automated playtesting — keeping the good ones and discarding the rest.

---

## How it works

Each iteration of the loop does five things:

1. **Retrieve** — pull the most relevant mechanics from the library as context for the model
2. **Propose** — ask Gemini to write a new mechanic as a Python function
3. **Compile** — check that the code runs without errors
4. **Playtest** — run 200+ simulated games to measure balance and strategic depth
5. **Verify** — accept, revise, or discard based on the scores

Accepted mechanics are added to a library and used as context for future proposals. The system also runs a **curriculum** that starts with simple single-rule mechanics and gradually unlocks more complex ones as the library grows.

---

## Modules

| File | Owner | What it does |
|---|---|---|
| `proposal_module.py` | Morgan | Prompts Gemini to propose a mechanic; repairs broken code up to 3× |
| `mechanic_library.py` | Benjamin / Ziyi | Stores accepted mechanics; retrieves similar ones via embedding search |
| `playtest_module.py` | Cody | Runs simulated games; measures balance and strategic depth |
| `verification_module.py` | Cody | Decides accept / revise / discard based on playtest scores |
| `curriculum.py` | Morgan | Tracks complexity stage; advances after 3 consecutive accepts |
| `base_game.py` | — | Defines the base 6×6 board game and agents |
| `compile_check.py` | — | Checks that a mechanic's Python code is valid before playtesting |
| `main.py` | — | Runs the full loop |

---

## Setup

### 1. Install dependencies

```bash
pip3 install -r requirements.txt
```

### 2. Authenticate with Google Cloud (Vertex AI)

This project uses Gemini via Vertex AI. You need the `gcloud` CLI installed and a Google Cloud project with Vertex AI enabled.

```bash
gcloud auth application-default login
```

Set your project:

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
```

> No API key needed — authentication is handled automatically via Application Default Credentials.

---

## Running

**Basic run (3 iterations):**
```bash
python3 main.py
```

**Custom run:**
```bash
python3 main.py --iterations 50 --top-k 3
```

**Clear the mechanic library:**
```bash
python3 -c "from mechanic_library import MechanicLibrary; MechanicLibrary().clear()"
```

---

## Evaluation scores

Each mechanic is scored on:

- **Compile check** — does the code parse and run without errors?
- **Playability** — do games complete without crashing or looping?
- **Balance** — do both players win roughly 50% of the time?
- **Depth** — does a smarter player (GreedyAgent) consistently beat a random one?
- **Aggregate** — weighted combination of balance and depth (threshold for acceptance)

---

## Related work

This project is inspired by [MORTAR](https://arxiv.org/abs/2601.00105) (Nasir et al., 2025), which evolves game mechanics using a quality-diversity algorithm, and [GAVEL](https://arxiv.org/abs/2407.09388) (Todd et al., NeurIPS 2024), which generates complete games in the Ludii game description language. DesignVoyager differs by using a curriculum and retrieval-based context instead of evolutionary search.
