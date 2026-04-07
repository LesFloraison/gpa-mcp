# RenderDoc MCP to GPA MCP Gap Analysis

This document is generated from local inspection of `renderdoc-mcp-master` and
local Intel GPA Frame Analyzer plugin probes.

## Current RenderDoc MCP Surface

RenderDoc live bridge tools:

- `get_capture_status`
- `find_events`
- `list_passes`
- `inspect_pipeline_state`
- `inspect_texture_usage`
- `inspect_shader`
- `get_shader_disasm`
- `inspect_mesh`
- `get_frame_packet`
- `get_pass_packet`
- `get_draw_packet`
- `debug_save_overlay`
- `debug_save_texture`

Offline bootstrap tools:

- `list_captures`
- `open_capture`
- `get_capture_status`

## RenderDoc Data Dependencies

- Capture/session facts: capture filename, API, loaded state.
- Action tree: events, event IDs, draw/dispatch/copy/clear flags, marker hierarchy.
- Pipeline state at an event: IA topology, shaders, stage resources, output merger targets.
- Shader reflection: entry point, stage bindings, signatures, constant blocks, variables.
- Shader disassembly: paginated disassembly text.
- Resource metadata: texture dimensions/format/sample count, buffer size.
- Resource usage: read/write producer/consumer events and usage kinds.
- Mesh data: vertex inputs, vertex/index buffers, post-VS data.
- Export/render helpers: texture export and debug overlay generation.
- D3D11 fixed-function state: blend, depth/stencil, rasterizer.

## GPA Validation Summary

Validated with:

```powershell
& 'C:\Program Files\IntelSWTools\GPA\FrameAnalyzer.exe' `
  --file_to_open 'D:\CDXrepo\renderdoc-mcp-master\bf1_2026_01_21__16_53_05.gpa_frame' `
  --py_plugin gpa_frame_export_probe `
  --py_arg 'D:\CDXrepo\renderdoc-mcp-master\gpa_frame_probe_export_escalated.json' `
  --output_log 'D:\CDXrepo\renderdoc-mcp-master\gpa_frame_probe_plugin_escalated.log'
```

Observed on the BF1 frame:

- API calls: 17,995
- Event calls: 1,313
- Resource groups: 1,134
- Resource views: 248
- Programs/shaders: 269
- Metrics: 13
- Event bindings include inputs, outputs, execution program, execution states, and metadata keys.
- Program descriptions include shader bytecode/disassembly-like DXBC text for captured programs.

Additional capability probe:

```powershell
& 'C:\Program Files\IntelSWTools\GPA\FrameAnalyzer.exe' `
  --file_to_open 'D:\CDXrepo\renderdoc-mcp-master\bf1_2026_01_21__16_53_05.gpa_frame' `
  --py_plugin gpa_capability_probe `
  --py_arg 'D:\CDXrepo\gpa-mcp\probe_outputs\bf1_capability_probe.json' `
  --output_log 'D:\CDXrepo\gpa-mcp\probe_outputs\bf1_capability_probe.log'
```

Observed in `bf1_capability_probe.json`:

- `ApiLog.get_calls()` returned 17,995 API calls and 1,313 events.
- GPA grouping modes all succeeded: Render Target, Command List, Shader Set, Pipeline State, Debug Region.
- 20/20 sampled event binding lookups succeeded.
- 20/20 sampled resource usage lookups succeeded.
- `Program.get_il_source(..., "isa")` succeeded for sampled compute programs.
- `Program.get_il_source(..., "dxil")` returned `Shader dumping is not available`, but DXBC-like shader text is already present in program descriptions.
- Sampled `execution.states` was present but empty for early compute dispatches; broader state field coverage still needs per-event probing.

Observed in `bf1_capability_probe_named.json`:

- First sampled `Dispatch` had CBV/SRV inputs, UAV texture outputs, program data, and empty states.
- First sampled `DrawIndexedInstanced` had CBV/VBV inputs, DSV/RTV texture outputs, program data, and populated states.
- Draw states included D3D11 blending state fields and related fixed-function state categories.
- Draw metadata included `input_geometry.vertex_buffers.layouts` with vertex buffer id, semantic names, offsets, and formats.

## Initial Feasibility Matrix

| RenderDoc MCP feature | GPA feasibility | Notes |
|---|---:|---|
| Open capture / status | Full | `FrameAnalyzer.exe --file_to_open` plus plugin execution works. |
| List capture files | Full | File-system scan for `.gpa_frame`; include both file-form and directory-form frames. |
| Find events | Full | GPA `ApiLog.get_calls()` returns event/call descriptions with IDs and names. |
| List passes / markers | Mostly full | GPA grouping modes work, including Debug Region. Need marker-name quality validation on more frames. |
| Frame packet | Mostly full | Event inventory and grouping are available. Need schema mapping for pass inventory. |
| Pass packet | Mostly full | Representative event and IO can be derived from groupings plus bindings. |
| Draw/dispatch packet | Mostly full | Event description, inputs/outputs, program, states, and metrics are available. Needs schema mapping. |
| Pipeline state summary | Mostly full | Event bindings expose execution states and bound resources. Need normalize GPA states to RenderDoc-like `ia/sh/res`. |
| Texture/resource usage | Mostly full | Bound inputs/outputs and sampled direct `get_usages()` calls work. Need broader texture-specific validation. |
| Shader summary | Mostly full | Program descriptions include stage records and shader text; signatures/cbuffers may need parsing from DXBC text. |
| Shader disassembly | Full for DXBC text, partial for IL API | DXBC text exists in program descriptions; ISA retrieval worked on samples; DXIL retrieval reported unavailable. |
| Mesh summary | Mostly full for layout/bindings, partial for post-VS | Draw bindings expose VBV resources and `input_geometry` layouts; post-VS data and full vertex-stream extraction are not yet proven. |
| Texture export | Partial | Plugin API exposes image data requests; export to PNG/DDS needs a GPA-side encoder and validation. |
| Debug overlays | Not equivalent yet | GPA plugin API does not expose RenderDoc-style debug overlays such as drawcall highlight/wireframe/overdraw in the observed API. |
| D3D11 fixed-function state | Mostly full | Draw-event `execution.states` includes D3D11 blend/fixed-function state fields; mapping to RenderDoc schema is still required. |

## Conclusion

GPA can reproduce almost all inspect/search/shader/resource-flow/pipeline-summary
behavior, and grouping support looks strong enough for pass-like tools. The main
unproven or non-equivalent areas are RenderDoc-style debug overlays, post-VS mesh
data, robust raw texture/buffer export, and exact DXIL dumping via the IL API.
The practical GPA MCP should start with:

- capture open/status/list
- event search
- draw/dispatch packet
- shader/program inspection
- resource binding and producer/consumer flow
- metrics summary

Then add mesh/raw resource/export capabilities only after targeted probes confirm
stable `get_subresource_data`, image data extraction, and input geometry metadata.
