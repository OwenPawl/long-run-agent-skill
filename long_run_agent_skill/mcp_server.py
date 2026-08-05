"""Local-stdio MCP server for durable long-running mission state."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .mcp_tools import MISSION_OPERATING_CONTRACT, register_mission_tools


def create_server() -> FastMCP:
    server = FastMCP(
        "long-run-agent",
        log_level="WARNING",
        instructions=MISSION_OPERATING_CONTRACT,
    )
    register_mission_tools(server)

    @server.prompt(name="mission_operating_contract")
    def mission_operating_contract() -> str:
        """Portable truth-preservation contract for long-running work."""
        return MISSION_OPERATING_CONTRACT

    return server


mcp = create_server()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
