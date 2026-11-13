"""Klarna-safe world on streamable HTTP, for MiMCP to proxy.

The upstream mini-agents entrypoint serves SSE only; MiMCP's upstream speaks
streamable HTTP. This reuses the same builder with the other transport.
"""

import os

from mini_agents.kernel.mcp_server import build_klarna_mcp


def main() -> None:
    mcp = build_klarna_mcp()
    mcp.settings.host = os.getenv("KLARNA_HOST", "0.0.0.0")
    mcp.settings.port = int(os.getenv("KLARNA_PORT", "9101"))
    print(
        f"klarna-safe (streamable http) on http://{mcp.settings.host}:{mcp.settings.port}/mcp",
        flush=True,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
