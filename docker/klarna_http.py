"""Streamable-HTTP launcher for the MiniKlarna MCP server.

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

from mini_agents.kernel.mcp_server import build_klarna_mcp


def main() -> None:
    mcp = build_klarna_mcp()
    mcp.settings.host = os.getenv("KLARNA_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("KLARNA_PORT", "9101"))
    mode = "unsafe" if "--unsafe" in sys.argv else "safe"
    print(
        f"klarna-{mode} (streamable http) on http://{mcp.settings.host}:{mcp.settings.port}/mcp",
        flush=True,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
