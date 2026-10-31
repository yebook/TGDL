"""Strict private native-cache protocol; never expose upstream payloads or identifiers."""
import re
from typing import Any


def count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def decode_cache_storage(payload: Any, generation: str) -> dict[str, Any]:
    if (not isinstance(payload, dict) or set(payload) != {"ok", "result"} or payload["ok"] is not True
            or not isinstance(payload["result"], dict)):
        raise ValueError("Invalid cache storage response")
    result = payload["result"]
    if (set(result) != {"generation", "scope", "scan_state", "usage", "operation"}
            or result["generation"] != generation or result["scope"] != "current_bot"
            or result["scan_state"] not in ("idle", "running", "ready", "failed")):
        raise ValueError("Invalid cache storage identity")
    usage = result["usage"]
    if usage is not None and (not isinstance(usage, dict) or set(usage) != {"used_bytes", "file_count"}
                              or not all(count(value) for value in usage.values())):
        raise ValueError("Invalid cache storage usage")
    operation = result["operation"]
    if operation is not None:
        if (not isinstance(operation, dict) or set(operation) != {
                "operation_id", "state", "before_bytes", "after_bytes", "released_bytes", "error_code"}
                or not isinstance(operation["operation_id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", operation["operation_id"])
                or operation["state"] not in ("running", "succeeded", "failed")
                or operation["error_code"] not in (None, "CACHE_SCAN_FAILED", "CACHE_CLEANUP_FAILED")
                or any(operation[key] is not None and not count(operation[key])
                       for key in ("before_bytes", "after_bytes", "released_bytes"))):
            raise ValueError("Invalid cache storage operation")
        if operation["state"] == "succeeded":
            before, after, released = (operation[key] for key in ("before_bytes", "after_bytes", "released_bytes"))
            if (before is None or after is None or released is None
                    or released != max(before - after, 0) or operation["error_code"] is not None):
                raise ValueError("Invalid completed cache operation")
        elif operation["after_bytes"] is not None or operation["released_bytes"] is not None:
            raise ValueError("Invalid pending cache result")
    return result
