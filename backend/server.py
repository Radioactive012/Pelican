from __future__ import annotations

import os
from importlib.metadata import version

from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse


BUILD_MARKER = os.getenv("BUILD_MARKER", "context-passport-v0-build-001")
server = MCPServer(
    name="context-passport",
    description="Context Passport remote MCP proof server",
    version="0.1.0",
)


@server.tool(description="Harmless connectivity check. Returns the supplied text without side effects.")
def ping(message: str = "pong") -> dict[str, str]:
    return {"ok": "true", "reply": message, "build": BUILD_MARKER}


@server.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "context-passport",
            "build": BUILD_MARKER,
            "mem0": version("mem0ai"),
            "mcp": version("mcp"),
        }
    )


@server.custom_route("/ready", methods=["GET"])
async def ready(_: Request) -> JSONResponse:
    required = ("GEMINI_API_KEY", "MONGODB_URI")
    missing = [name for name in required if not os.getenv(name)]
    return JSONResponse(
        {"status": "ready" if not missing else "configuration_required", "missing": missing},
        status_code=200 if not missing else 503,
    )


app = server.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    host=os.getenv("HOST", "0.0.0.0"),
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))
