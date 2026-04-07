from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable

from gpa_mcp.core.adapter import GpaMcpAdapter, envelope


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "open_capture",
        "description": "Open a GPA .gpa_frame capture and build the local analysis cache.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "get_capture_status",
        "description": "Return the current GPA capture status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_events",
        "description": "Find GPA API events by text, pass/group marker, and event id range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "marker": {"type": "string"},
                "eid_min": {"type": "integer"},
                "eid_max": {"type": "integer"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "list_passes",
        "description": "List pass-like GPA grouping ranges.",
        "inputSchema": {
            "type": "object",
            "properties": {"marker": {"type": "string"}, "limit": {"type": "integer", "default": 50}},
        },
    },
    {
        "name": "get_draw_packet",
        "description": "Return a compact GPA draw or dispatch packet.",
        "inputSchema": {
            "type": "object",
            "properties": {"eid": {"type": "integer"}},
            "required": ["eid"],
        },
    },
    {
        "name": "inspect_shader",
        "description": "Return GPA shader/program details for one event and stage.",
        "inputSchema": {
            "type": "object",
            "properties": {"eid": {"type": "integer"}, "stage": {"type": "string"}},
            "required": ["eid", "stage"],
        },
    },
    {
        "name": "inspect_texture_usage",
        "description": "Return GPA resource usage for one texture/resource id.",
        "inputSchema": {
            "type": "object",
            "properties": {"rid": {"type": "string"}, "limit": {"type": "integer", "default": 20}},
            "required": ["rid"],
        },
    },
    {
        "name": "inspect_pipeline_state",
        "description": "Return compact GPA pipeline state for one event.",
        "inputSchema": {
            "type": "object",
            "properties": {"eid": {"type": "integer"}},
            "required": ["eid"],
        },
    },
]


class GpaToolRegistry:
    def __init__(self, adapter: GpaMcpAdapter | None = None) -> None:
        self.adapter = adapter or GpaMcpAdapter()
        self.handlers: dict[str, ToolHandler] = {
            "open_capture": self._open_capture,
            "get_capture_status": self._get_capture_status,
            "find_events": self._find_events,
            "list_passes": self._list_passes,
            "get_draw_packet": self._get_draw_packet,
            "inspect_shader": self._inspect_shader,
            "inspect_texture_usage": self._inspect_texture_usage,
            "inspect_pipeline_state": self._inspect_pipeline_state,
        }

    def invoke(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            if method not in self.handlers:
                return envelope(False, err={"code": "unknown_tool", "msg": method})
            return self.handlers[method](params or {})
        except Exception as exc:
            return envelope(False, err={"code": "tool_error", "msg": str(exc)})

    def _open_capture(self, params: dict[str, Any]) -> dict[str, Any]:
        path = params.get("path")
        if not path:
            return envelope(False, err={"code": "missing_args", "msg": "path is required"})
        return self.adapter.open_capture(str(path))

    def _get_capture_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return self.adapter.get_capture_status()

    def _find_events(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.adapter.find_events(
            q=params.get("q"),
            marker=params.get("marker"),
            eid_min=params.get("eid_min"),
            eid_max=params.get("eid_max"),
            limit=int(params.get("limit", 50) or 50),
        )

    def _list_passes(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.adapter.list_passes(marker=params.get("marker") or params.get("pass"), limit=int(params.get("limit", 50) or 50))

    def _get_draw_packet(self, params: dict[str, Any]) -> dict[str, Any]:
        if "eid" not in params:
            return envelope(False, err={"code": "missing_args", "msg": "eid is required"})
        return self.adapter.get_draw_packet(int(params["eid"]))

    def _inspect_shader(self, params: dict[str, Any]) -> dict[str, Any]:
        if "eid" not in params or "stage" not in params:
            return envelope(False, err={"code": "missing_args", "msg": "eid and stage are required"})
        return self.adapter.inspect_shader(int(params["eid"]), str(params["stage"]))

    def _inspect_texture_usage(self, params: dict[str, Any]) -> dict[str, Any]:
        rid = params.get("rid")
        if rid is None:
            return envelope(False, err={"code": "missing_args", "msg": "rid is required"})
        return self.adapter.inspect_texture_usage(str(rid), limit=int(params.get("limit", 20) or 20))

    def _inspect_pipeline_state(self, params: dict[str, Any]) -> dict[str, Any]:
        if "eid" not in params:
            return envelope(False, err={"code": "missing_args", "msg": "eid is required"})
        return self.adapter.inspect_pipeline_state(int(params["eid"]))


def _configure_stdio() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def maybe_create_fastmcp() -> Any | None:
    try:
        from fastmcp import FastMCP  # type: ignore
    except ImportError:
        return None

    registry = GpaToolRegistry()
    app = FastMCP(name="GPA MCP")

    @app.tool(description="Open a GPA .gpa_frame capture and build the local analysis cache.")
    def open_capture(path: str) -> Any:
        return registry.invoke("open_capture", {"path": path})

    @app.tool(description="Return the current GPA capture status.")
    def get_capture_status() -> Any:
        return registry.invoke("get_capture_status")

    @app.tool(description="Find GPA API events by text, pass/group marker, and event id range.")
    def find_events(
        q: str | None = None,
        marker: str | None = None,
        eid_min: int | None = None,
        eid_max: int | None = None,
        limit: int = 50,
    ) -> Any:
        return registry.invoke("find_events", {"q": q, "marker": marker, "eid_min": eid_min, "eid_max": eid_max, "limit": limit})

    @app.tool(description="List pass-like GPA grouping ranges.")
    def list_passes(marker: str | None = None, limit: int = 50) -> Any:
        return registry.invoke("list_passes", {"marker": marker, "limit": limit})

    @app.tool(description="Return a compact GPA draw or dispatch packet.")
    def get_draw_packet(eid: int) -> Any:
        return registry.invoke("get_draw_packet", {"eid": eid})

    @app.tool(description="Return GPA shader/program details for one event and stage.")
    def inspect_shader(eid: int, stage: str) -> Any:
        return registry.invoke("inspect_shader", {"eid": eid, "stage": stage})

    @app.tool(description="Return GPA resource usage for one texture/resource id.")
    def inspect_texture_usage(rid: str, limit: int = 20) -> Any:
        return registry.invoke("inspect_texture_usage", {"rid": rid, "limit": limit})

    @app.tool(description="Return compact GPA pipeline state for one event.")
    def inspect_pipeline_state(eid: int) -> Any:
        return registry.invoke("inspect_pipeline_state", {"eid": eid})

    return app


def _read_mcp_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        key, _, value = line.decode("ascii", errors="replace").partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0") or "0")
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def _write_mcp_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


def _mcp_result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _mcp_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def run_stdio_fallback_mcp() -> int:
    registry = GpaToolRegistry()
    while True:
        message = _read_mcp_message()
        if message is None:
            return 0

        message_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "notifications/initialized":
            continue
        if method == "initialize":
            _write_mcp_message(
                _mcp_result(
                    message_id,
                    {
                        "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "gpa-mcp", "version": "0.1.0"},
                    },
                )
            )
            continue
        if method == "ping":
            _write_mcp_message(_mcp_result(message_id, {}))
            continue
        if method == "tools/list":
            _write_mcp_message(_mcp_result(message_id, {"tools": TOOL_DEFINITIONS}))
            continue
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            result = registry.invoke(str(name), arguments)
            _write_mcp_message(
                _mcp_result(
                    message_id,
                    {
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                        "isError": not bool(result.get("ok")),
                    },
                )
            )
            continue
        if message_id is not None:
            _write_mcp_message(_mcp_error(message_id, -32601, f"Method not found: {method}"))


def run_local_json(method: str, params: dict[str, Any]) -> int:
    registry = GpaToolRegistry()
    result = registry.invoke(method, params)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def main() -> int:
    _configure_stdio()

    parser = argparse.ArgumentParser(description="GPA MCP runtime")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_local = sub.add_parser("run-local-json")
    run_local.add_argument("method")
    run_local.add_argument("--params", default="{}")
    run_local.add_argument("--params-file")

    run_mcp = sub.add_parser("run-mcp")
    run_mcp.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    run_mcp.add_argument("--host", default="127.0.0.1")
    run_mcp.add_argument("--port", type=int, default=8766)

    args = parser.parse_args()

    if args.cmd == "run-local-json":
        if args.params_file:
            payload = json.loads(open(args.params_file, "r", encoding="utf-8-sig").read())
            params = payload.get("params", payload)
        else:
            params = json.loads(args.params)
        return run_local_json(args.method, params)

    app = maybe_create_fastmcp()
    if app is None:
        if args.transport == "stdio":
            return run_stdio_fallback_mcp()
        print(json.dumps(envelope(False, err={"code": "fastmcp_not_installed", "msg": "fastmcp is not installed"}), ensure_ascii=False, indent=2))
        return 1

    if args.transport == "http":
        app.run("http", host=args.host, port=args.port)
    else:
        app.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
