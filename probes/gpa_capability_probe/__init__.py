import json
import os
import time
import traceback

from utils import common
import plugin_api
from plugin_api.api_log import GroupingType


DEFAULT_OUTPUT_PATH = r"D:\CDXrepo\gpa-mcp\probe_outputs\gpa_capability_probe.json"


def _safe_repr(value, limit=1200):
    try:
        text = repr(value)
    except Exception as exc:
        text = "<repr failed: {}>".format(exc)
    if len(text) > limit:
        return text[:limit] + "...<truncated>"
    return text


def _safe_desc(obj):
    if obj is None:
        return None
    try:
        return obj.get_description()
    except Exception as exc:
        return {"error": _safe_repr(exc)}


def _count_calls(nodes):
    try:
        return len(list(common.all_calls_from_node(nodes)))
    except Exception as exc:
        return {"error": _safe_repr(exc)}


def _first_call_desc(nodes):
    try:
        for call in common.all_calls_from_node(nodes):
            return _safe_desc(call)
    except Exception as exc:
        return {"error": _safe_repr(exc)}
    return None


def _probe_groupings(api_log):
    results = {}
    for grouping in GroupingType:
        try:
            nodes = api_log.get_calls(grouping)
            results[grouping.value] = {
                "ok": True,
                "root_count": len(nodes),
                "call_count": _count_calls(nodes),
                "first": _first_call_desc(nodes),
            }
        except Exception as exc:
            results[grouping.value] = {"ok": False, "error": _safe_repr(exc)}
    return results


def _probe_resource_usage(resources_accessor, max_resources=20):
    out = []
    memory_resources = resources_accessor.get_memory_resources()
    for resource_id, views in memory_resources.items():
        for view in views:
            if len(out) >= max_resources:
                return out
            desc = _safe_desc(view)
            item = {"resource_id_key": resource_id, "description": desc}
            try:
                item["usages"] = view.get_usages()[:50]
                item["usage_ok"] = True
            except Exception as exc:
                item["usage_ok"] = False
                item["usage_error"] = _safe_repr(exc)
            out.append(item)
    return out


def _probe_event_bindings(api_log, max_events=20):
    calls = list(common.all_calls_from_node(api_log.get_calls()))
    out = []
    for call in calls:
        desc = _safe_desc(call)
        if not desc or not desc.get("is_event"):
            continue
        item = {"call": desc}
        try:
            bindings = call.get_bindings()
            item["ok"] = True
            item["binding_keys"] = sorted(bindings.keys())
            item["input_descriptions"] = [_safe_desc(x) for x in bindings.get("inputs", [])[:8]]
            item["output_descriptions"] = [_safe_desc(x) for x in bindings.get("outputs", [])[:8]]
            item["execution"] = {
                key: _safe_desc(value) for key, value in bindings.get("execution", {}).items()
            }
            item["metadata"] = bindings.get("metadata", {})
        except Exception as exc:
            item["ok"] = False
            item["error"] = _safe_repr(exc)
        out.append(item)
        if len(out) >= max_events:
            break
    return out


def _probe_named_events(api_log):
    calls = list(common.all_calls_from_node(api_log.get_calls()))
    wanted = {
        "first_dispatch": lambda name: "dispatch" in name.lower(),
        "first_draw": lambda name: name.lower().startswith("draw") or "draw" in name.lower(),
        "first_indexed_draw": lambda name: "drawindexed" in name.lower(),
    }
    out = {}
    for label, predicate in wanted.items():
        for call in calls:
            desc = _safe_desc(call)
            if not desc or not desc.get("is_event"):
                continue
            name = str(desc.get("name") or "")
            if not predicate(name):
                continue
            item = {"call": desc}
            try:
                bindings = call.get_bindings()
                item["ok"] = True
                item["binding_keys"] = sorted(bindings.keys())
                item["input_descriptions"] = [_safe_desc(x) for x in bindings.get("inputs", [])[:16]]
                item["output_descriptions"] = [_safe_desc(x) for x in bindings.get("outputs", [])[:16]]
                item["execution"] = {
                    key: _safe_desc(value) for key, value in bindings.get("execution", {}).items()
                }
                item["metadata"] = bindings.get("metadata", {})
            except Exception as exc:
                item["ok"] = False
                item["error"] = _safe_repr(exc)
            out[label] = item
            break
    return out


def _probe_programs(resources_accessor, max_programs=20):
    out = []
    for program in resources_accessor.get_programs()[:max_programs]:
        desc = _safe_desc(program)
        item = {"description": desc}
        for shader_type in ("vertex", "pixel", "compute"):
            if shader_type not in desc:
                continue
            item.setdefault("il_sources", {})
            for il_type in ("dxil", "isa"):
                try:
                    text = program.get_il_source(shader_type, il_type, timeout_ms=60000)
                    item["il_sources"][shader_type + ":" + il_type] = {
                        "ok": True,
                        "length": len(text or ""),
                        "preview": (text or "")[:500],
                    }
                except Exception as exc:
                    item["il_sources"][shader_type + ":" + il_type] = {
                        "ok": False,
                        "error": _safe_repr(exc),
                    }
        out.append(item)
    return out


def run(output_path: "Output JSON path" = DEFAULT_OUTPUT_PATH):
    started = time.time()
    result = {"ok": False, "started_at": started, "output_path": output_path}
    try:
        api_log = plugin_api.get_api_log_accessor()
        resources = plugin_api.get_resources_accessor()
        metrics = plugin_api.get_metrics_accessor()
        calls = list(common.all_calls_from_node(api_log.get_calls()))
        event_calls = [call for call in calls if _safe_desc(call).get("is_event")]

        result.update(
            {
                "ok": True,
                "elapsed_seconds": time.time() - started,
                "api_call_count": len(calls),
                "event_call_count": len(event_calls),
                "groupings": _probe_groupings(api_log),
                "event_bindings": _probe_event_bindings(api_log),
                "named_event_bindings": _probe_named_events(api_log),
                "resource_usage_samples": _probe_resource_usage(resources),
                "program_samples": _probe_programs(resources),
                "metric_descriptions": metrics.get_metrics_descriptions(),
            }
        )
    except Exception:
        result["error"] = traceback.format_exc()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as out:
        json.dump(result, out, indent=2, ensure_ascii=False)

    try:
        if result.get("ok") and calls:
            return common.node_to_result(calls[0], common.MessageSeverity.INFO, "GPA capability probe completed")
    except Exception:
        pass
    return []


def desc():
    return {
        "name": "GPA Capability Probe",
        "description": "Checks GPA Frame Analyzer plugin coverage for MCP feature parity.",
        "apis": [],
        "applicabilities": ["Apilog", "Resources"],
        "plugin_api_version": "1.2",
    }
