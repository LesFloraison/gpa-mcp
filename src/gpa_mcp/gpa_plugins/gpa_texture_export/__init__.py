import binascii
import json
import os
import struct
import sys
import time
import traceback


GPA_PLUGIN_ROOT = r"C:\Program Files\IntelSWTools\GPA\python_plugins"
if GPA_PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, GPA_PLUGIN_ROOT)

from plugin_api.resources import ImageRequest
from utils import common
import plugin_api


DEFAULT_REQUEST_PATH = r"D:\CDXrepo\gpa-mcp\.state\requests\export_texture.json"


def _safe_desc(obj):
    if obj is None:
        return None
    try:
        return obj.get_description()
    except Exception as exc:
        return {"error": repr(exc)}


def _normalize_rid(rid):
    text = str(rid)
    if "::" in text:
        return text.split("::")[-1]
    return text


def _usage_eid(usage):
    if isinstance(usage, int):
        return usage
    if isinstance(usage, dict):
        for key in ("eventId", "event_id", "eid", "id"):
            if key in usage:
                return int(usage[key])
    if isinstance(usage, (list, tuple)) and usage:
        try:
            return int(usage[0])
        except Exception:
            return None
    return None


def _find_call(api_log, eid):
    if eid is None:
        return None
    for call in common.all_calls_from_node(api_log.get_calls()):
        desc = call.get_description()
        if desc.get("id") == int(eid):
            return call
    return None


def _candidate_priority(desc):
    view_type = str(desc.get("view_type") or "").upper()
    if view_type == "RTV":
        return 0
    if view_type == "SRV":
        return 1
    if view_type == "UAV":
        return 2
    if view_type == "DSV":
        return 3
    return 4


def _select_view(resources_accessor, rid, requested_view_type):
    rid_key = _normalize_rid(rid)
    requested = str(requested_view_type or "").upper()
    matches = []
    for resource_id, views in resources_accessor.get_memory_resources().items():
        if _normalize_rid(resource_id) != rid_key:
            continue
        for view in views:
            desc = _safe_desc(view) or {}
            if desc.get("resource_type") != "texture":
                continue
            view_type = str(desc.get("view_type") or "").upper()
            if requested and view_type != requested:
                continue
            usages = []
            try:
                usages = view.get_usages() or []
            except Exception:
                usages = []
            last_eid = max([_usage_eid(item) or -1 for item in usages], default=-1)
            matches.append((last_eid, -_candidate_priority(desc), view, desc, usages))

    if not matches:
        raise RuntimeError("No texture view matched resource id {}".format(rid))
    matches.sort(reverse=True)
    _, _, view, desc, usages = matches[0]
    return view, desc, usages


def _resolve_call(api_log, usages, requested_eid):
    if requested_eid is not None:
        call = _find_call(api_log, int(requested_eid))
        if call is None:
            raise RuntimeError("Event {} was not found".format(requested_eid))
        return call, int(requested_eid)

    eids = [_usage_eid(item) for item in usages]
    eids = [eid for eid in eids if eid is not None]
    if not eids:
        raise RuntimeError("The selected texture view has no tracked usages")
    eid = max(eids)
    call = _find_call(api_log, eid)
    if call is None:
        raise RuntimeError("Event {} was not found".format(eid))
    return call, int(eid)


def _png_chunk(tag, data):
    crc = binascii.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _adler32(data):
    mod = 65521
    a = 1
    b = 0
    for offset in range(0, len(data), 5552):
        chunk = data[offset : offset + 5552]
        for value in chunk:
            a = (a + value) % mod
            b = (b + a) % mod
    return (b << 16) | a


def _zlib_store(data):
    output = bytearray(b"\x78\x01")
    offset = 0
    total = len(data)
    while offset < total:
        block = data[offset : offset + 65535]
        offset += len(block)
        final = 1 if offset >= total else 0
        output.append(final)
        output += struct.pack("<H", len(block))
        output += struct.pack("<H", 0xFFFF - len(block))
        output += block
    output += struct.pack(">I", _adler32(data))
    return bytes(output)


def _write_png_rgba8(path, width, height, rows):
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + row for row in rows)
    idat = _zlib_store(raw)
    with open(path, "wb") as handle:
        handle.write(header)
        handle.write(_png_chunk(b"IHDR", ihdr))
        handle.write(_png_chunk(b"IDAT", idat))
        handle.write(_png_chunk(b"IEND", b""))


def _float_to_u8(value):
    if value != value:
        value = 0.0
    if value < 0.0:
        value = 0.0
    if value > 1.0:
        value = 1.0
    return int(round(value * 255.0))


def _decode_rows_as_rgba(desc, payload, mip):
    width = int(desc["mips"][mip]["width"])
    height = int(desc["mips"][mip]["height"])
    row_pitch = int(payload.row_pitch)
    data = payload.data
    fmt = str(desc.get("format") or "").upper()

    rows = []
    if fmt in {"R8G8B8A8_UNORM", "R8G8B8A8_UNORM_SRGB"}:
        row_bytes = width * 4
        for y in range(height):
            start = y * row_pitch
            rows.append(bytes(data[start : start + row_bytes]))
    elif fmt in {"B8G8R8A8_UNORM", "B8G8R8A8_UNORM_SRGB"}:
        row_bytes = width * 4
        for y in range(height):
            start = y * row_pitch
            src = data[start : start + row_bytes]
            row = bytearray(row_bytes)
            for x in range(0, row_bytes, 4):
                b = src[x]
                g = src[x + 1]
                r = src[x + 2]
                a = src[x + 3]
                row[x : x + 4] = bytes((r, g, b, a))
            rows.append(bytes(row))
    elif fmt in {"B8G8R8X8_UNORM", "B8G8R8X8_UNORM_SRGB"}:
        row_bytes = width * 4
        for y in range(height):
            start = y * row_pitch
            src = data[start : start + row_bytes]
            row = bytearray(row_bytes)
            for x in range(0, row_bytes, 4):
                b = src[x]
                g = src[x + 1]
                r = src[x + 2]
                row[x : x + 4] = bytes((r, g, b, 255))
            rows.append(bytes(row))
    elif fmt == "R10G10B10A2_UNORM":
        packed_row_bytes = width * 4
        for y in range(height):
            start = y * row_pitch
            src = data[start : start + packed_row_bytes]
            row = bytearray(width * 4)
            for x in range(width):
                value = struct.unpack_from("<I", bytes(src), x * 4)[0]
                r = value & 0x3FF
                g = (value >> 10) & 0x3FF
                b = (value >> 20) & 0x3FF
                a = (value >> 30) & 0x3
                row[x * 4 + 0] = (r * 255 + 511) // 1023
                row[x * 4 + 1] = (g * 255 + 511) // 1023
                row[x * 4 + 2] = (b * 255 + 511) // 1023
                row[x * 4 + 3] = (a * 255 + 1) // 3
            rows.append(bytes(row))
    elif fmt == "R16G16B16A16_FLOAT":
        pixel_bytes = 8
        row_bytes = width * pixel_bytes
        for y in range(height):
            start = y * row_pitch
            src = data[start : start + row_bytes]
            row = bytearray(width * 4)
            for x in range(width):
                base = x * pixel_bytes
                r = struct.unpack("<e", bytes(src[base : base + 2]))[0]
                g = struct.unpack("<e", bytes(src[base + 2 : base + 4]))[0]
                b = struct.unpack("<e", bytes(src[base + 4 : base + 6]))[0]
                a = struct.unpack("<e", bytes(src[base + 6 : base + 8]))[0]
                row[x * 4 + 0] = _float_to_u8(r)
                row[x * 4 + 1] = _float_to_u8(g)
                row[x * 4 + 2] = _float_to_u8(b)
                row[x * 4 + 3] = _float_to_u8(a)
            rows.append(bytes(row))
    elif fmt in {"R16_FLOAT", "D16_UNORM", "R32_FLOAT", "D32_FLOAT"}:
        bytes_per_pixel = 2 if fmt in {"R16_FLOAT", "D16_UNORM"} else 4
        row_bytes = width * bytes_per_pixel
        for y in range(height):
            start = y * row_pitch
            src = data[start : start + row_bytes]
            row = bytearray(width * 4)
            for x in range(width):
                base = x * bytes_per_pixel
                if fmt == "R16_FLOAT":
                    value = struct.unpack("<e", bytes(src[base : base + 2]))[0]
                elif fmt == "D16_UNORM":
                    value = struct.unpack("<H", bytes(src[base : base + 2]))[0] / 65535.0
                else:
                    value = struct.unpack("<f", bytes(src[base : base + 4]))[0]
                u8 = _float_to_u8(value)
                row[x * 4 : x * 4 + 4] = bytes((u8, u8, u8, 255))
            rows.append(bytes(row))
    else:
        raise RuntimeError("PNG export is not implemented for format {}".format(fmt))

    return rows, width, height, row_pitch, fmt


def _write_raw(path, payload):
    with open(path, "wb") as handle:
        handle.write(payload.data)


def _load_request(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _write_result(request, result):
    result_path = request.get("result_path")
    if result_path:
        os.makedirs(os.path.dirname(result_path), exist_ok=True)
        with open(result_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)


def run(request_path: "Path to a request JSON file" = DEFAULT_REQUEST_PATH):
    started = time.time()
    request = None
    result = {"ok": False, "request_path": request_path, "started_at": started}
    call = None
    try:
        request = _load_request(request_path)
        api_log = plugin_api.get_api_log_accessor()
        resources = plugin_api.get_resources_accessor()

        rid = request["rid"]
        mip = int(request.get("mip", 0) or 0)
        slice_ = int(request.get("slice", 0) or 0)
        extract_before = bool(request.get("extract_before", False))
        output_path = request["output_path"]
        container = str(request.get("container", "png") or "png").lower()

        view, desc, usages = _select_view(resources, rid, request.get("view_type"))
        call, resolved_eid = _resolve_call(api_log, usages, request.get("eid"))
        payload_map = resources.get_images_data([ImageRequest(view, mip, slice_, call, extract_before=extract_before)], timeout=120000)
        payload = payload_map[next(iter(payload_map))]

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        if container == "raw":
            _write_raw(output_path, payload)
            width = int(desc["mips"][mip]["width"])
            height = int(desc["mips"][mip]["height"])
            row_pitch = int(payload.row_pitch)
        elif container == "png":
            rows, width, height, row_pitch, _ = _decode_rows_as_rgba(desc, payload, mip)
            _write_png_rgba8(output_path, width, height, rows)
        else:
            raise RuntimeError("Unsupported container {}".format(container))

        result.update(
            {
                "ok": True,
                "elapsed_seconds": time.time() - started,
                "rid": _normalize_rid(rid),
                "eid": resolved_eid,
                "extract_before": extract_before,
                "mip": mip,
                "slice": slice_,
                "container": container,
                "output_path": output_path,
                "resource": desc,
                "width": width,
                "height": height,
                "row_pitch": row_pitch,
            }
        )
        _write_result(request, result)
        return common.node_to_result(call, common.MessageSeverity.INFO, "Texture exported")
    except Exception:
        result["error"] = traceback.format_exc()
        if request is not None:
            result["request"] = request
            _write_result(request, result)
        return [] if call is None else common.node_to_result(call, common.MessageSeverity.ERROR, "Texture export failed")


def desc():
    return {
        "name": "GPA Texture Export",
        "description": "Exports a GPA texture resource to PNG or raw bytes.",
        "apis": [],
        "applicabilities": ["Apilog", "Resources"],
        "plugin_api_version": "1.2",
    }
