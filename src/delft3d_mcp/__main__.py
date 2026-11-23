"""CLI entrypoint for delft3d-mcp stdio MCP server."""

from __future__ import annotations

import inspect
import os

from delft3d_mcp.logging_setup import setup_logging
from delft3d_mcp.server import mcp


def main() -> None:
    os.environ.setdefault("FASTMCP_QUIET", "1")
    setup_logging()
    sig = inspect.signature(mcp.run)
    if "show_banner" in sig.parameters:
        mcp.run(transport="stdio", show_banner=False)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
