import json
import os
import time
import traceback

from plugin_api.api_call import ApiCall
from plugin_api.group import Group
from plugin_api.api_log import GroupingType
from utils import common
import plugin_api


DEFAULT_OUTPUT_PATH = r"D:\CDXrepo\gpa-mcp\.state\exports\active_gpa_frame.json"


def _safe_repr(value, limit=2000):
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


def _serialize_node(node):
    if isinstance(node, Group):
        children = [_serialize_node(child) for child in node.get_children()]
        event_ids = []
        for child in children:
            if child.get("event_ids"):
                event_ids.extend(child["event_ids"])
            elif child.get("eid") is not None:
                event_ids.append(child["eid"])
        return {
            "type": "group",
            "name": node.get_name(),
            "children": children,
            "event_ids": event_ids,
            "start_eid": min(event_ids) if event_ids else None,
            "end_eid": max(event_ids) if event_ids else None,
        }
    if isinstance(node, ApiCall):
        desc = _safe_desc(node) or {}
        return {
            "type": "call",
            "eid": desc.get("id"),
            "name": desc.get("name"),
            "is_event": desc.get("is_event"),
            "event_ids": [desc.get("id")] if desc.get("id") is not None else [],
        }
    return {"type": "unknown", "repr": _safe_repr(node)}


def _program_ref(program_desc):
    if not isinstance(program_desc, dict):
        return None
    return {
        "id": program_desc.get("id"),
        "stages": [key for key in program_desc.keys() if key != "id"],
    }


def _binding_for_call(call):
    desc = _safe_desc(call) or {}
    item = {"eid": desc.get("id"), "ok": False}
    try:
        bindings = call.get_bindings()
        execution = {}
        program = bindings.get("execution", {}).get("program")
        states = bindings.get("execution", {}).get("states")
        program_desc = _safe_desc(program)
        states_desc = _safe_desc(states) or {}
        if program_desc:
            execution["program"] = _program_ref(program_desc)
        if states_desc is not None:
            execution["states"] = states_desc
        item.update(
            {
                "ok": True,
                "inputs": [_safe_desc(resource) for resource in bindings.get("inputs", [])],
                "outputs": [_safe_desc(resource) for resource in bindings.get("outputs", [])],
                "execution": execution,
                "metadata": bindings.get("metadata", {}),
            }
        )
    except Exception as exc:
        item["error"] = _safe_repr(exc)
    return item


def _resource_table(resources_accessor):
    table = {}
    for resource_id, views in resources_accessor.get_memory_resources().items():
        entries = []
        for view in views:
            desc = _safe_desc(view)
            item = {"description": desc}
            try:
                item["usages"] = view.get_usages()
            except Exception as exc:
                item["usage_error"] = _safe_repr(exc)
                item["usages"] = []
            entries.append(item)
        table[str(resource_id)] = entries
    return table


def _program_table(resources_accessor):
    table = {}
    for program in resources_accessor.get_programs():
        desc = _safe_desc(program) or {}
        if desc.get("id") is not None:
            table[str(desc["id"])] = desc
    return table


def run(output_path: "Output JSON path" = DEFAULT_OUTPUT_PATH):
    started = time.time()
    result = {"ok": False, "started_at": started, "output_path": output_path}
    calls = []
    try:
        api_log = plugin_api.get_api_log_accessor()
        resources = plugin_api.get_resources_accessor()
        metrics = plugin_api.get_metrics_accessor()

        calls = list(common.all_calls_from_node(api_log.get_calls()))
        call_descriptions = [_safe_desc(call) for call in calls]
        event_calls = [call for call, desc in zip(calls, call_descriptions) if desc and desc.get("is_event")]
        groupings = {}
        for grouping in GroupingType:
            try:
                nodes = api_log.get_calls(grouping)
                groupings[grouping.value] = [_serialize_node(node) for node in nodes]
            except Exception as exc:
                groupings[grouping.value] = {"error": _safe_repr(exc)}

        result.update(
            {
                "ok": True,
                "elapsed_seconds": time.time() - started,
                "api_call_count": len(calls),
                "event_call_count": len(event_calls),
                "calls": call_descriptions,
                "groupings": groupings,
                "bindings": {str((_safe_desc(call) or {}).get("id")): _binding_for_call(call) for call in event_calls},
                "resources": _resource_table(resources),
                "programs": _program_table(resources),
                "metrics": metrics.get_metrics_descriptions(),
            }
        )
    except Exception:
        result["error"] = traceback.format_exc()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as out:
        json.dump(result, out, indent=2, ensure_ascii=False)

    try:
        if result.get("ok") and calls:
            return common.node_to_result(calls[0], common.MessageSeverity.INFO, "GPA MCP export completed")
    except Exception:
        pass
    return []


def desc():
    return {
        "name": "GPA MCP Export",
        "description": "Exports compact frame data for the GPA MCP server.",
        "apis": [],
        "applicabilities": ["Apilog", "Resources"],
        "plugin_api_version": "1.2",
    }
