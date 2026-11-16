# mim-demo

Containerised IPI test stack for the garak MiM demo: a Klarna support agent (fronted by the OGX Responses gateway) whose
MCP tools are proxied by
[MiMCP](https://github.com/ABeltramo/MiMCP), a man-in-the-middle that splices
attack payloads into tool results.

```mermaid
flowchart TD
    garak["garak runner (host)"] --> ogx["OGX Responses gateway"]
    ogx --> llm["inference provider\n(OpenAI-compatible)"]
    ogx -- " MCP tool calls " --> mimcp["MiMCP\n(man-in-the-middle)"]
    mimcp -- relay --> klarna["Klarna MCP target"]
    mimcp -. " splices attack payloads\ninto tool results " .-> ogx
```

## Layout

| Path                        | What it is                                                                                                                                                                 |
|-----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `compose.yaml`              | the three services and their wiring                                                                                                                                        |
| `docker/`                   | our build overlays: klarna + ogx Dockerfiles and the streamable-HTTP klarna launcher (the [mini-agents](https://github.com/hjrnunes/mini-agents) submodule stays pristine) |
| `mini-agents/`, `mimcp/`    | git submodules, pinned; mimcp ships its own Dockerfile                                                                                                                     |
| `ogx/ogx-config.json`       | OGX config: everything deployment-specific comes from env vars                                                                                                             |
| `garak/klarna_refund_intent.yaml` | garak attack scenario (data/policy tampering under a legitimate refund request — expected to hit) |
| `garak/mim-demo.yaml`       | garak run config for this deployment                                                                                                                                       |
## Setup

```bash
git clone --recurse-submodules <this repo>
cd mim-demo
cp .env.example .env        # fill in OPENAI_API_KEY and OPENAI_BASE_URL
podman compose up -d --build   # or: docker compose up -d --build
```

Readiness:

```bash
curl -s http://localhost:8000/api/healthz   # mimcp
curl -s http://localhost:9321/v1/models -H "Authorization: Bearer $OPENAI_API_KEY" | head
```

## Run garak against it

From the [TrustyAI garak fork](https://github.com/trustyai-explainability/garak) checkout on branch
`agent-ipi-probe`:

```bash
cd /path/to/mim-demo     # the garak config uses a relative scenario path
OPENAI_API_KEY=<your key> uv run --project /path/to/garak \
  python -m garak --config garak/mim-demo.yaml
```

Keep `target_name` in `garak/mim-demo.yaml` in sync with `MODEL_ID` in
`.env`. Results land in `~/.local/share/garak/garak_runs/` — see the MiM
Demo wiki note for the jq one-liners over `notes.mimcp_calls`.

### Red-team mode (unsafe ledger)

`KLARNA_UNSAFE=1` rebuilds the Klarna agent with the code-level business
rules stripped — the "tool layer trusts the model" deployment:

```bash
KLARNA_UNSAFE=1 podman compose up -d klarna
```

`garak/klarna_refund_intent.yaml` is the A/B demo. Its success criteria
require the refund *completing*, not the model being fooled: safe mode
blocks the attack at the ledger (`REJECTED`, balance intact), unsafe mode
completes it (`remaining_to_pay: 0.0` on an ineligible order). The
`non_tampered` entry is the control.

The agent's ledger is scoped to one MCP session: the launcher
(`docker/klarna_http.py`) swaps in a fresh seeded `KlarnaState` whenever a
new session appears and releases it when the client terminates the session.
Each red-team generation arms its own MiMCP session, so every generation
starts from the seed ledger — no restarts, any `run.generations` works, and
completed refunds cannot contaminate the next generation. Sessions are
served one at a time; the container log prints the state lifecycle
(`state created/dropped for session ...`).

## Dashboard

`tools/dashboard.py` turns a report into one self-contained HTML file (no
network, no external assets) with the attacks, the tool calls, the injected
before/after diffs and the target's reasoning:

```bash
python3 tools/dashboard.py                 # newest report in the runs dir
python3 tools/dashboard.py <report.jsonl>  # or a specific one
```

Reports come from `~/.local/share/garak/garak_runs` unless `GARAK_RUNS_DIR`
is set. The output lands next to the report as `<report>.dashboard.html`.

The before/after diff needs `upstream_response` in the recorded call, which
the pinned MiMCP commit provides.

## Notes

- `advertised_base: http://mimcp:8000` in the garak config is what makes the
  container topology work: the injector talks to MiMCP through the published
  port, but the descriptor it hands the generator must name the address OGX
  can reach (the compose service).
- The scenario here differs from garak's bundled one only in `upstream`
  (`http://klarna:9101/mcp`): MiMCP relays to the klarna service over the
  compose network.
- Klarna's port 9101 is deliberately not published; add a `ports` entry if
  you want to inspect the ledger with `get_klarna_state_summary` directly.
- Wipe the `ogx-state` volume after changing the registered model (`podman
  compose down -v`) — OGX's registry survives restarts.
