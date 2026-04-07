from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_DIR = REPO_ROOT / ".state"
EXPORT_DIR = STATE_DIR / "exports"
STATE_PATH = STATE_DIR / "active_capture.json"
PLUGIN_NAME = "gpa_mcp_export"


def envelope(ok: bool, data: Any = None, err: dict[str, Any] | None = None, truncated: bool = False, count: int | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"truncated": truncated}
    if count is not None:
        meta["count"] = count
    return {"ok": ok, "mode": "summary", "data": data, "err": err, "meta": meta}


@dataclass(slots=True)
class ActiveCapture:
    cap: str
    path: str
    name: str
    size: int
    mtime: str
    export_path: str
    log_path: str | None
    opened_at: str
    api: str | None = None
    api_call_count: int | None = None
    event_call_count: int | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ActiveCapture":
        return cls(**data)

    def to_json(self) -> dict[str, Any]:
        return {
            "cap": self.cap,
            "path": self.path,
            "name": self.name,
            "size": self.size,
            "mtime": self.mtime,
            "export_path": self.export_path,
            "log_path": self.log_path,
            "opened_at": self.opened_at,
            "api": self.api,
            "api_call_count": self.api_call_count,
            "event_call_count": self.event_call_count,
        }


class GpaMcpAdapter:
    def __init__(self, frame_analyzer: str | None = None) -> None:
        self.frame_analyzer = Path(frame_analyzer) if frame_analyzer else self._discover_frame_analyzer()

    def get_capture_status(self) -> dict[str, Any]:
        state = self._load_state()
        if state is None:
            return envelope(True, {"loaded": False})
        return envelope(
            True,
            {
                "loaded": True,
                "cap": state.cap,
                "path": state.path,
                "name": state.name,
                "size": state.size,
                "mtime": state.mtime,
                "api": state.api,
                "api_call_count": state.api_call_count,
                "event_call_count": state.event_call_count,
                "export_path": state.export_path,
            },
        )

    def open_capture(self, path: str) -> dict[str, Any]:
        capture_path = Path(path)
        if not capture_path.exists():
            return envelope(False, err={"code": "capture_not_found", "msg": f"Capture not found: {path}"})
        if capture_path.suffix.lower() != ".gpa_frame":
            return envelope(False, err={"code": "invalid_capture_type", "msg": f"Expected .gpa_frame: {path}"})
        if not self.frame_analyzer.exists():
            return envelope(False, err={"code": "frame_analyzer_not_found", "msg": str(self.frame_analyzer)})

        self._install_export_plugin()
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        cap_id = self._cap_id(capture_path)
        export_path = EXPORT_DIR / f"{cap_id}.json"
        log_path = EXPORT_DIR / f"{cap_id}.log"

        command = [
            str(self.frame_analyzer),
            "--file_to_open",
            str(capture_path),
            "--py_plugin",
            PLUGIN_NAME,
            "--py_arg",
            str(export_path),
            "--output_log",
            str(log_path),
        ]
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300, check=False)
        if completed.returncode != 0:
            return envelope(
                False,
                err={
                    "code": "frame_analyzer_failed",
                    "msg": completed.stderr or completed.stdout or f"exit code {completed.returncode}",
                },
            )
        if not export_path.exists():
            return envelope(
                False,
                err={
                    "code": "export_missing",
                    "msg": f"GPA export plugin did not create {export_path}",
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                },
            )

        frame = self._load_frame(export_path)
        if not frame.get("ok"):
            return envelope(False, data={"export_path": str(export_path)}, err={"code": "export_failed", "msg": frame.get("error")})

        stat = capture_path.stat()
        state = ActiveCapture(
            cap=cap_id,
            path=str(capture_path),
            name=capture_path.name,
            size=stat.st_size if capture_path.is_file() else 0,
            mtime=datetime.fromtimestamp(stat.st_mtime).isoformat(),
            export_path=str(export_path),
            log_path=str(log_path) if log_path.exists() else None,
            opened_at=datetime.now().isoformat(),
            api=self._infer_api(frame),
            api_call_count=int(frame.get("api_call_count") or 0),
            event_call_count=int(frame.get("event_call_count") or 0),
        )
        self._save_state(state)
        return envelope(True, {**state.to_json(), "verified": True})

    def find_events(
        self,
        q: str | None = None,
        marker: str | None = None,
        eid_min: int | None = None,
        eid_max: int | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        frame = self._require_frame()
        query = (q or "").lower()
        marker_filter = (marker or "").lower()
        items = []
        marker_by_eid = self._marker_by_eid(frame)
        for call in frame.get("calls", []):
            if not call:
                continue
            eid = int(call.get("id") or 0)
            name = str(call.get("name") or "")
            marker_path = marker_by_eid.get(eid, "")
            if eid_min is not None and eid < int(eid_min):
                continue
            if eid_max is not None and eid > int(eid_max):
                continue
            haystack = f"{name} {marker_path}".lower()
            if query and query not in haystack:
                continue
            if marker_filter and marker_filter not in marker_path.lower():
                continue
            items.append({"eid": eid, "name": name, "type": self._event_type(name), "marker": marker_path})
            if len(items) >= limit:
                break
        return envelope(True, {"count": len(items), "items": items}, truncated=len(items) >= limit, count=len(items))

    def list_passes(self, marker: str | None = None, limit: int = 50) -> dict[str, Any]:
        frame = self._require_frame()
        marker_l = (marker or "").lower()
        groups = self._pass_groups(frame)
        items = []
        for group in groups:
            name = str(group.get("name") or "")
            if marker_l and marker_l not in name.lower():
                continue
            event_ids = [int(eid) for eid in group.get("event_ids", []) if eid is not None]
            items.append(
                {
                    "eid": group.get("start_eid"),
                    "pass": name,
                    "stats": self._stats_for_event_ids(frame, event_ids),
                    "end_eid": group.get("end_eid"),
                    "grouping": group.get("grouping"),
                }
            )
            if len(items) >= limit:
                break
        return envelope(True, {"count": len(items), "items": items}, truncated=len(items) >= limit, count=len(items))

    def inspect_pipeline_state(self, eid: int) -> dict[str, Any]:
        frame = self._require_frame()
        binding = self._binding(frame, eid)
        if binding is None:
            return envelope(False, err={"code": "event_not_found", "msg": f"No binding for event {eid}"})
        inputs = binding.get("inputs", [])
        outputs = binding.get("outputs", [])
        program = (binding.get("execution") or {}).get("program") or {}
        states = (binding.get("execution") or {}).get("states") or {}
        data = {
            "eid": int(eid),
            "api": self._infer_api(frame),
            "ia": self._input_geometry(binding),
            "sh": self._shader_summary(frame, program),
            "res": self._resource_counts(inputs, outputs),
            "state": self._state_summary(states),
        }
        return envelope(True, data)

    def inspect_shader(self, eid: int, stage: str) -> dict[str, Any]:
        frame = self._require_frame()
        binding = self._binding(frame, eid)
        if binding is None:
            return envelope(False, err={"code": "event_not_found", "msg": f"No binding for event {eid}"})
        program_ref = (binding.get("execution") or {}).get("program") or {}
        program = self._program(frame, program_ref.get("id"))
        stage_key = self._stage_key(stage)
        if not program or stage_key not in program:
            return envelope(False, err={"code": "no_shader", "msg": f"No {stage} shader at event {eid}"})
        shader = program.get(stage_key) or {}
        dxbc = str(shader.get("dxbc") or shader.get("source") or "")
        data = {
            "eid": int(eid),
            "stage": stage_key,
            "shader": {
                "program_id": program.get("id"),
                "id": shader.get("id"),
                "hash_upper": shader.get("hash_upper"),
                "hash_lower": shader.get("hash_lower"),
            },
            "bind": self._binding_counts_for_stage(binding, stage_key),
            "bindings": self._stage_bindings(binding, stage_key),
            "cbufs": self._parse_cbuffer_summary(dxbc),
            "sig": self._parse_signature_summary(dxbc),
            "code": self._code_window(dxbc, 0, 80),
        }
        return envelope(True, data)

    def inspect_texture_usage(self, rid: str, limit: int = 20) -> dict[str, Any]:
        frame = self._require_frame()
        rid_key = self._rid_key(rid)
        resource = self._resource_views(frame, rid_key)
        if not resource:
            return envelope(False, err={"code": "resource_not_found", "msg": f"Resource not found: {rid}"})
        usages = []
        reads = 0
        writes = 0
        for view in resource:
            desc = view.get("description") or {}
            view_kind = self._view_usage_kind(desc.get("view_type"))
            for use in view.get("usages", []):
                use_kind = view_kind if view_kind != "other" else self._usage_kind(str(use))
                if use_kind == "read":
                    reads += 1
                elif use_kind == "write":
                    writes += 1
                eid = self._usage_eid(use)
                if eid is not None:
                    call = self._call(frame, eid)
                    usages.append(
                        {
                            "eid": eid,
                            "type": use_kind,
                            "usage": str(use),
                            "name": (call or {}).get("name"),
                            "view_type": desc.get("view_type"),
                        }
                    )
        usages.sort(key=lambda item: int(item.get("eid") or 0))
        first_desc = resource[0].get("description") or {}
        return envelope(
            True,
            {
                "rid": rid_key,
                "name": first_desc.get("name"),
                "meta": self._resource_meta(first_desc),
                "uses": {"read": reads, "write": writes},
                "items": usages[:limit],
            },
            truncated=len(usages) > limit,
            count=len(usages),
        )

    def get_draw_packet(self, eid: int) -> dict[str, Any]:
        frame = self._require_frame()
        call = self._call(frame, eid)
        binding = self._binding(frame, eid)
        if call is None or binding is None:
            return envelope(False, err={"code": "event_not_found", "msg": f"Event not found: {eid}"})
        name = str(call.get("name") or "")
        context = self._event_context(frame, eid)
        packet = {
            "eid": int(eid),
            "name": name,
            "type": self._event_type(name),
            "context": context,
            "counts": self._draw_counts(call),
            "pipe": self.inspect_pipeline_state(eid)["data"],
            "shader": None,
            "io": self._event_io(binding),
            "state": self._state_summary((binding.get("execution") or {}).get("states") or {}),
        }
        default_stage = "cs" if packet["type"] == "Dispatch" else "ps"
        shader = self.inspect_shader(eid, default_stage)
        if shader["ok"]:
            packet["shader"] = shader["data"]
        return envelope(True, packet)

    def _discover_frame_analyzer(self) -> Path:
        candidates = [
            Path(r"C:\Program Files\IntelSWTools\GPA\FrameAnalyzer.exe"),
            Path(r"C:\Program Files (x86)\IntelSWTools\GPA\FrameAnalyzer.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _install_export_plugin(self) -> None:
        src = REPO_ROOT / "plugins" / PLUGIN_NAME / "__init__.py"
        dst_dir = Path.home() / "Documents" / "GPA" / "python_plugins" / PLUGIN_NAME
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst_dir / "__init__.py")

    def _load_state(self) -> ActiveCapture | None:
        if not STATE_PATH.exists():
            return None
        return ActiveCapture.from_json(json.loads(STATE_PATH.read_text(encoding="utf-8")))

    def _save_state(self, state: ActiveCapture) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _require_frame(self) -> dict[str, Any]:
        state = self._load_state()
        if state is None:
            raise RuntimeError("No GPA frame is active. Call open_capture first.")
        return self._load_frame(Path(state.export_path))

    @staticmethod
    def _load_frame(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    @staticmethod
    def _cap_id(path: Path) -> str:
        return "gpa_" + hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _infer_api(frame: dict[str, Any]) -> str | None:
        for call in frame.get("calls", []):
            for arg in (call or {}).get("arguments", []):
                text = str(arg.get("type", "")) + " " + str(arg.get("value", ""))
                if "D3D12" in text:
                    return "D3D12"
                if "D3D11" in text or "ID3D11" in text:
                    return "D3D11"
        return None

    @staticmethod
    def _event_type(name: str) -> str:
        lower = name.lower()
        if "dispatch" in lower:
            return "Dispatch"
        if "draw" in lower:
            return "Draw"
        if "clear" in lower:
            return "Clear"
        if "copy" in lower or "resolve" in lower:
            return "Copy"
        return "Action"

    def _call(self, frame: dict[str, Any], eid: int) -> dict[str, Any] | None:
        for call in frame.get("calls", []):
            if call and int(call.get("id") or -1) == int(eid):
                return call
        return None

    @staticmethod
    def _binding(frame: dict[str, Any], eid: int) -> dict[str, Any] | None:
        return frame.get("bindings", {}).get(str(eid))

    @staticmethod
    def _program(frame: dict[str, Any], program_id: Any) -> dict[str, Any] | None:
        if program_id is None:
            return None
        return frame.get("programs", {}).get(str(program_id))

    @staticmethod
    def _stage_key(stage: str) -> str:
        mapping = {"vs": "vertex", "ps": "pixel", "cs": "compute", "vertex": "vertex", "pixel": "pixel", "compute": "compute"}
        return mapping.get(str(stage).lower(), str(stage).lower())

    def _shader_summary(self, frame: dict[str, Any], program_ref: dict[str, Any]) -> dict[str, Any]:
        program = self._program(frame, program_ref.get("id"))
        if not program:
            return {}
        out = {}
        for key in ("vertex", "hull", "domain", "geometry", "pixel", "compute"):
            if key in program:
                out[key] = {"program_id": program.get("id"), "shader_id": (program.get(key) or {}).get("id")}
        return out

    @staticmethod
    def _resource_counts(inputs: list[dict[str, Any]], outputs: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"srv": 0, "uav": 0, "cbv": 0, "vbv": 0, "ibv": 0, "rt": 0, "ds": 0}
        for item in inputs + outputs:
            view = str((item or {}).get("view_type") or "").upper()
            if view == "SRV":
                counts["srv"] += 1
            elif view == "UAV":
                counts["uav"] += 1
            elif view == "CBV":
                counts["cbv"] += 1
            elif view == "VBV":
                counts["vbv"] += 1
            elif view == "IBV":
                counts["ibv"] += 1
            elif view == "RTV":
                counts["rt"] += 1
            elif view == "DSV":
                counts["ds"] += 1
        return counts

    @staticmethod
    def _input_geometry(binding: dict[str, Any]) -> dict[str, Any]:
        meta = binding.get("metadata") or {}
        return meta.get("input_geometry") or {}

    @staticmethod
    def _state_summary(states: dict[str, Any]) -> dict[str, Any]:
        return {
            "available": bool(states),
            "groups": sorted(states.keys())[:80],
            "values": states,
        }

    @staticmethod
    def _binding_counts_for_stage(binding: dict[str, Any], stage_key: str) -> dict[str, int]:
        # GPA binding descriptions do not always carry stage names, so report event-level counts.
        return GpaMcpAdapter._resource_counts(binding.get("inputs", []), binding.get("outputs", []))

    @staticmethod
    def _stage_bindings(binding: dict[str, Any], stage_key: str) -> dict[str, list[dict[str, Any]]]:
        buckets = {"srv": [], "uav": [], "cbv": [], "vbv": [], "ibv": [], "rtv": [], "dsv": []}
        for item in binding.get("inputs", []) + binding.get("outputs", []):
            view = str((item or {}).get("view_type") or "").lower()
            if view in buckets:
                buckets[view].append(item)
        return buckets

    @staticmethod
    def _code_window(text: str, offset: int, max_lines: int) -> dict[str, Any]:
        lines = [line for line in text.splitlines() if line.strip()]
        window = lines[offset : offset + max_lines]
        return {
            "target": "DXBC" if text else None,
            "line_count": len(lines),
            "offset": offset,
            "returned": len(window),
            "truncated": offset + len(window) < len(lines),
            "text": "\n".join(window),
        }

    @staticmethod
    def _parse_cbuffer_summary(dxbc: str) -> list[dict[str, Any]]:
        cbufs = []
        current = None
        for line in dxbc.splitlines():
            stripped = line.strip("/ ")
            if stripped.startswith("cbuffer "):
                current = {"slot": len(cbufs), "name": stripped.split(" ", 1)[1].strip(), "variables": [], "vars": 0}
                cbufs.append(current)
            elif current and "Offset:" in stripped and "Size:" in stripped:
                parts = stripped.split(";")[0].split()
                name = parts[1] if len(parts) > 1 else parts[0]
                current["variables"].append({"name": name, "raw": stripped})
                current["vars"] = len(current["variables"])
        return cbufs[:32]

    @staticmethod
    def _parse_signature_summary(dxbc: str) -> dict[str, list[dict[str, Any]]]:
        return {"inputs": [], "outputs": []}

    @staticmethod
    def _draw_counts(call: dict[str, Any]) -> dict[str, int | None]:
        out = {"idx": None, "inst": None}
        for arg in call.get("arguments", []):
            name = str(arg.get("name") or "").lower()
            if "indexcount" in name or name == "vertexcount":
                out["idx"] = int(arg.get("value") or 0)
            elif "instancecount" in name:
                out["inst"] = int(arg.get("value") or 0)
        return out

    @staticmethod
    def _rid_key(rid: str) -> str:
        text = str(rid)
        if "::" in text:
            return text.split("::")[-1]
        return text

    @staticmethod
    def _resource_views(frame: dict[str, Any], rid_key: str) -> list[dict[str, Any]]:
        return frame.get("resources", {}).get(str(rid_key), [])

    @staticmethod
    def _resource_meta(desc: dict[str, Any]) -> dict[str, Any]:
        meta = {"resource_type": desc.get("resource_type"), "view_type": desc.get("view_type")}
        for key in ("format", "texture_type", "mips", "size", "offset", "stride", "final_fb"):
            if key in desc:
                meta[key] = desc[key]
        return meta

    @staticmethod
    def _usage_eid(use: Any) -> int | None:
        if isinstance(use, int):
            return use
        if isinstance(use, dict):
            for key in ("eventId", "event_id", "eid", "id"):
                if key in use:
                    return int(use[key])
        try:
            if isinstance(use, (list, tuple)) and use:
                return int(use[0])
        except Exception:
            pass
        return None

    @staticmethod
    def _usage_kind(usage: str) -> str:
        write_tokens = ["uav", "rtv", "dsv", "target", "write", "copydst", "cle"]
        read_tokens = ["srv", "cbv", "vbv", "ibv", "resource", "read", "copysrc"]
        lower = usage.lower()
        if any(token in lower for token in write_tokens):
            return "write"
        if any(token in lower for token in read_tokens):
            return "read"
        return "other"

    @staticmethod
    def _view_usage_kind(view_type: Any) -> str:
        view = str(view_type or "").upper()
        if view in {"RTV", "DSV", "UAV"}:
            return "write"
        if view in {"SRV", "CBV", "VBV", "IBV"}:
            return "read"
        return "other"

    def _event_io(self, binding: dict[str, Any]) -> dict[str, Any]:
        return {
            "in": binding.get("inputs", []),
            "out": binding.get("outputs", []),
            "metadata": binding.get("metadata", {}),
        }

    def _pass_groups(self, frame: dict[str, Any]) -> list[dict[str, Any]]:
        for grouping_name in ("Debug Region", "Render Target", "Shader Set", "Pipeline State"):
            roots = frame.get("groupings", {}).get(grouping_name)
            if not isinstance(roots, list):
                continue
            groups = []
            self._collect_groups(roots, grouping_name, groups)
            if groups:
                return groups
        return []

    def _collect_groups(self, nodes: list[dict[str, Any]], grouping_name: str, out: list[dict[str, Any]]) -> None:
        for node in nodes:
            if node.get("type") == "group":
                node = {**node, "grouping": grouping_name}
                out.append(node)
                self._collect_groups(node.get("children", []), grouping_name, out)

    def _stats_for_event_ids(self, frame: dict[str, Any], event_ids: list[int]) -> dict[str, int]:
        stats = {"draw": 0, "dispatch": 0, "copy": 0, "clear": 0, "action": 0, "child": len(event_ids)}
        for eid in event_ids:
            call = self._call(frame, eid)
            kind = self._event_type(str((call or {}).get("name") or ""))
            key = kind.lower()
            if key in stats:
                stats[key] += 1
            else:
                stats["action"] += 1
        return stats

    def _marker_by_eid(self, frame: dict[str, Any]) -> dict[int, str]:
        mapping = {}
        for group in self._pass_groups(frame):
            name = str(group.get("name") or "")
            for eid in group.get("event_ids", []) or []:
                if eid is not None and int(eid) not in mapping:
                    mapping[int(eid)] = name
        return mapping

    def _event_context(self, frame: dict[str, Any], eid: int) -> dict[str, Any]:
        marker_by_eid = self._marker_by_eid(frame)
        event_calls = [call for call in frame.get("calls", []) if (call or {}).get("is_event")]
        eids = [int(call.get("id")) for call in event_calls]
        idx = eids.index(int(eid)) if int(eid) in eids else -1
        return {
            "marker_path": marker_by_eid.get(int(eid), ""),
            "position": {"index": idx + 1 if idx >= 0 else None, "count": len(eids)},
            "neighbors": {
                "prev": self._compact_call(self._call(frame, eids[idx - 1])) if idx > 0 else None,
                "next": self._compact_call(self._call(frame, eids[idx + 1])) if idx >= 0 and idx + 1 < len(eids) else None,
            },
        }

    def _compact_call(self, call: dict[str, Any] | None) -> dict[str, Any] | None:
        if not call:
            return None
        name = str(call.get("name") or "")
        return {"eid": call.get("id"), "name": name, "type": self._event_type(name)}
