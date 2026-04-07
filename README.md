# GPA MCP

Minimal Intel GPA MCP prototype for analyzing `.gpa_frame` captures through
Graphics Frame Analyzer's Python Plugin API.

This directory is intentionally separate from `D:\CDXrepo\renderdoc-mcp-master`.

## Implemented Tools

- `open_capture`: opens a `.gpa_frame` with `FrameAnalyzer.exe`, runs the export plugin, and caches a JSON snapshot under `.state/exports`.
- `find_events`: searches API/event calls by name, event id range, and grouping marker text.
- `list_passes`: lists GPA grouping ranges. It prefers `Debug Region`, then falls back to `Render Target`, `Shader Set`, and `Pipeline State`.
- `get_draw_packet`: returns a RenderDoc-style draw/dispatch packet with context, counts, pipeline, shader, resources, and fixed-function state.
- `inspect_shader`: returns shader identity, bindings, and a DXBC text window for a selected stage.
- `inspect_texture_usage`: returns resource metadata plus read/write event usage derived from GPA resource views.
- `inspect_pipeline_state`: returns IA geometry, shader summary, resource counts, and D3D state groups.

Auxiliary tool: `get_capture_status`.

## CLI Examples

```powershell
$env:PYTHONPATH='D:\CDXrepo\gpa-mcp\src'
python -m gpa_mcp.server.runtime run-local-json open_capture --params '{"path":"D:\\CDXrepo\\renderdoc-mcp-master\\bf1_2026_01_21__16_53_05.gpa_frame"}'
python -m gpa_mcp.server.runtime run-local-json find_events --params-file 'D:\CDXrepo\gpa-mcp\fixtures\find_drawindexed.json'
python -m gpa_mcp.server.runtime run-local-json inspect_pipeline_state --params-file 'D:\CDXrepo\gpa-mcp\fixtures\eid_481.json'
```

## MCP Server

```powershell
$env:PYTHONPATH='D:\CDXrepo\gpa-mcp\src'
python -m gpa_mcp.server.runtime run-mcp --transport stdio
```

The stdio server has a built-in JSON-RPC fallback and does not require `fastmcp`.
Install the optional `fastmcp` extra only if you want the FastMCP-backed HTTP
transport.

## Notes

- `open_capture` requires Intel GPA's `FrameAnalyzer.exe`; the adapter auto-discovers the default install path.
- The export plugin is copied to `%USERPROFILE%\Documents\GPA\python_plugins\gpa_mcp_export` before opening a capture.
- The current prototype focuses on structured data extraction. RenderDoc debug overlays and image export are not implemented yet.
