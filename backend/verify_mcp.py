from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def verify(url: str) -> dict[str, object]:
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("ping", {"message": "remote-gate"})
            return {
                "ok": not result.is_error,
                "server": initialized.server_info.name,
                "version": initialized.server_info.version,
                "tools": [tool.name for tool in tools.tools],
                "ping": result.structured_content,
            }


if __name__ == "__main__":
    base_url = os.getenv("RENDER_EXTERNAL_URL", "http://127.0.0.1:8000").rstrip("/")
    report = asyncio.run(verify(f"{base_url}/mcp"))
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["ok"] and "ping" in report["tools"] else 1)
