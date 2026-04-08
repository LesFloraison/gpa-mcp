# GPA MCP

Minimal Intel GPA MCP prototype for analyzing `.gpa_frame` captures through
Graphics Frame Analyzer's Python Plugin API.


## Implemented Tools

- `open_capture`: opens a `.gpa_frame` with `FrameAnalyzer.exe`, runs the packaged GPA export plugin, and caches a JSON snapshot under `.state/exports`.
- `find_events`: searches API/event calls by name, event id range, and grouping marker text.
- `list_passes`: lists GPA grouping ranges. It prefers `Debug Region`, then falls back to `Render Target`, `Shader Set`, and `Pipeline State`.
- `get_draw_packet`: returns a RenderDoc-style draw/dispatch packet with context, counts, pipeline, shader, resources, and fixed-function state.
- `inspect_shader`: returns shader identity, bindings, and a DXBC text window for a selected stage.
- `inspect_texture_usage`: returns resource metadata plus read/write event usage derived from GPA resource views.
- `inspect_pipeline_state`: returns IA geometry, shader summary, resource counts, and D3D state groups.
- `export_texture`: exports a texture resource for a specific event/mip/slice to `.png` or raw bytes using GPA `get_subresource_data`.

Auxiliary tool: `get_capture_status`.

## CLI Examples

Run these commands from the repository root. Install or refresh the user-level tool after local changes:

```powershell
uv tool install --reinstall .
```

```powershell
gpa-mcp run-local-json open_capture --params '{"path":"./bf1_2026_02_27__10_31_27.gpa_frame"}'
gpa-mcp run-local-json find_events --params-file './fixtures/find_drawindexed.json'
gpa-mcp run-local-json inspect_pipeline_state --params-file './fixtures/eid_481.json'
gpa-mcp run-local-json export_texture --params-file './fixtures/export_texture_474.json'
```

## MCP Server

```powershell
gpa-mcp run-mcp --transport stdio
```

`fastmcp` is a required dependency and is installed with the `gpa-mcp` uv tool.
The Codex MCP config should call the tool executable directly instead of a bare
Python interpreter:

```toml
[mcp_servers.gpa-mcp]
args = ["run-mcp", "--transport", "stdio"]
command = "gpa-mcp"
enabled = true
```

## Notes

- `open_capture` requires Intel GPA's `FrameAnalyzer.exe`; the adapter auto-discovers the default install path.
- GPA plugins are packaged under `src/gpa_mcp/gpa_plugins` and copied to `%USERPROFILE%\Documents\GPA\python_plugins\...` before opening/exporting a capture.
- Runtime state is written under the current working directory in `.state/`, including:
  - frame exports in `.state/exports`
  - texture export requests/results in `.state/requests`
  - texture outputs in `.state/textures`
- `export_texture` can target a specific event with `eid`, or if `eid` is omitted it uses the texture view's last tracked usage.
- `export_texture` currently writes `.png` for common color/depth formats such as `R8G8B8A8`, `B8G8R8A8`, `R10G10B10A2`, `R16G16B16A16_FLOAT`, `R16_FLOAT`, `R32_FLOAT`, `D16_UNORM`, and `D32_FLOAT`.
- For formats that are not mapped to PNG yet, use `container="raw"` to export the exact subresource bytes plus metadata.
