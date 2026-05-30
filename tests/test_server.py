"""Minimal MCP server for testing the proxy."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test-server")


@mcp.tool(description="Add two numbers together.")
def add(a: int, b: int):
    """Add two numbers."""
    return f"{a + b}"


@mcp.tool(description="Echo a message back.")
def echo(message: str):
    """Echo a message."""
    return f"Echo: {message}"


@mcp.tool(description="Deliberately fail.")
def fail() -> str:
    """Always errors."""
    from mcp.server.fastmcp.exceptions import ToolError

    raise ToolError("This tool always fails")


if __name__ == "__main__":
    mcp.run()
