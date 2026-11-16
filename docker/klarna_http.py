"""Streamable-HTTP launcher for the MiniKlarna MCP server.

State is scoped per MCP session: when a request arrives for a session the
server has not just served, a fresh seeded KlarnaState is swapped in, and
the old backing is released on session termination (HTTP DELETE). Tool
side effects never leak across sessions, so every red-team generation
starts from the seed ledger.

The MCP SDK runs tool calls in a per-session task whose context predates
the session id, so the swap is driven from the HTTP layer (which sees the
Mcp-Session-Id header on every request) rather than request context
propagation. This assumes sessions are served one at a time, which holds
for the red-team pipeline; concurrent sessions would share whichever
backing was swapped in last.

KLARNA_UNSAFE=1 swaps in the red-team executor: same tool surface, the
code-level business rules (eligibility, HITL threshold, ownership) are
stripped. The unsafe mode stands in for a deployment where the tool layer
trusts the model.
"""

import os
import sys

if os.getenv("KLARNA_UNSAFE") == "1":
    # mcp_server reads this at import time
    sys.argv.append("--unsafe")

import uvicorn

from mini_agents.domains.klarna.state import KlarnaState
from mini_agents.kernel.mcp_server import build_klarna_mcp


class SessionScopedState:
    """Delegate KlarnaState access to the current session's backing."""

    def __init__(self, factory):
        self._factory = factory
        self._current = factory()
        self._current_session_id = None

    def observe(self, session_id):
        """Swap in a fresh backing when a different session arrives."""
        if session_id is None or session_id == self._current_session_id:
            return
        self._current_session_id = session_id
        self._current = self._factory()
        print(f"state created for session {session_id}", flush=True)

    def close(self, session_id):
        """Release the backing when the client terminates the session."""
        if session_id != self._current_session_id:
            return
        print(f"state dropped for session {session_id}", flush=True)
        self._current_session_id = None
        self._current = self._factory()

    def __getattr__(self, name):
        return getattr(self._current, name)


class SessionScopeMiddleware:
    """Track the MCP session per request; drop state on session DELETE."""

    def __init__(self, app, state):
        self.app = app
        self.state = state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode().lower(): value.decode()
            for key, value in scope.get("headers", [])
        }
        session_id = headers.get("mcp-session-id")
        if scope.get("method") == "DELETE" and session_id:
            self.state.close(session_id)
        else:
            self.state.observe(session_id)
        await self.app(scope, receive, send)


def main() -> None:
    state = SessionScopedState(KlarnaState)
    mcp = build_klarna_mcp(state=state)
    host = os.getenv("KLARNA_HOST", "0.0.0.0")
    port = int(os.getenv("KLARNA_PORT", "9101"))
    mode = "unsafe" if "--unsafe" in sys.argv else "safe"
    print(
        f"klarna-{mode} (streamable http, per-session state) on http://{host}:{port}/mcp",
        flush=True,
    )
    app = SessionScopeMiddleware(mcp.streamable_http_app(), state)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
