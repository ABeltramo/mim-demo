# AGENTS.md — guidance for AI agents working in mim-demo

## What this repo is

A shareable, containerised demo of indirect prompt injection against an
agentic target. garak (TrustyAI fork, branch `agent-ipi-probe`) drives it:
see the README there for the probe/injector/detector side (`probes/midojo.py`,
`injectors/mimcp.py`, `detectors/midojo.py`). This repo is only the
deployment: the target stack, the garak run config, and the scenarios.

## Hard constraints

- `mini-agents/` and `mimcp/` are git submodules. Keep `mini-agents`
  pristine — never commit changes there. Behaviour changes to the Klarna
  agent belong in `docker/klarna_http.py` (our launcher), which injects
  state and patches the executor at import time.
- No secrets or internal product names in this repo. Credentials come from
  `.env` (never committed; `.env.example` is the template).
- No absolute paths in anything committed; the garak run config is executed
  from this directory, so scenario paths stay relative where possible.

## The launcher (`docker/klarna_http.py`)

Three responsibilities, all implemented without touching mini-agents:

1. **Mode**: `KLARNA_UNSAFE` (default 1) selects the red-team executor by
   appending `--unsafe` before importing `mcp_server` (it reads `sys.argv`
   at import time). In unsafe mode the executor subclass also strips the
   `_unsafe_note` key from tool replies — the target must not see the tell.
2. **Per-session ledger**: `SessionScopedState` delegates state access to a
   current backing; the ASGI middleware swaps in a fresh seeded
   `KlarnaState` on `Mcp-Session-Id` change and releases it on DELETE.
   ContextVar propagation does NOT reach tool execution (the SDK runs tools
   in a per-session task whose context predates the session id) — do not
   "fix" this back to request context. Sessions are assumed sequential.
3. **Server**: runs `mcp.streamable_http_app()` under uvicorn directly.

## Running an evaluation

```bash
cd <this repo>
OPENAI_API_KEY=<key> uv run --project /path/to/garak \
  python -m garak --config garak/mim-demo.yaml
```

- `garak/mim-demo.yaml` points at the scenario; scenarios are data —
  attack design changes go there, not in garak code.
- Flip ledger mode with `KLARNA_UNSAFE=0 podman compose up -d klarna`
  (compose must recreate the container; check `podman logs` for the
  `klarna-safe`/`klarna-unsafe` banner and `state created/dropped` lines).
- Each generation arms its own MiMCP session and gets a fresh ledger, so
  `run.generations` works in both modes.
- `tools/dashboard.py <report.jsonl>` writes `<report>.dashboard.html`;
  reports live in `~/.local/share/garak/garak_runs/`.

## Scenario file format

See `garak/klarna_refund_intent.yaml`: `benign_task` (the customer's
legitimate request), `instructions` (the agent's system prompt — the
prompt-level defense under test), and `attacks` with per-tool `behavior`
(MiMCP `inject` with `structured_patch`/`template`/`resync_text`) and
`success` criteria (`tool_called` with `args`/`response_contains`,
`output_contains*`, `any_of`/`all_of`/`not`). A no-`tools` attack is the
control: the agent must refuse it.

## Gotchas

- After changing `MODEL_ID`, wipe OGX's registry: `podman compose down -v`.
- `advertised_base: http://mimcp:8000` must name the compose service —
  MiMCP derives the URL it advertises to OGX from it.
- Klarna's port 9101 is deliberately unpublished; publish it to inspect the
  ledger with `get_klarna_state_summary` directly.
- Model behaviour is non-deterministic: single-generation results are
  noise. Use `run.generations: 3` before drawing conclusions.

## Commits

Discuss changes with the repo owner before committing and pushing; the
owner runs manual validations between changes. No co-author trailers;
keep commit messages factual and short.
