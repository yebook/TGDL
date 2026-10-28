"""Plan safe basenames once, independently of subsequent configuration edits."""
import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath
from string import Formatter
from zoneinfo import ZoneInfo

from app.core.templates import render_template
from app.db.models import MediaItem, MediaRequest
from app.storage.files import safe_name


def text_summary(value: str | None) -> str:
    """Retain a bounded, plain single-line message summary without hidden controls."""
    text = " ".join("".join(" " if unicodedata.category(char).startswith("C") else char
                            for char in (value or "")).split())
    return text if len(text) <= 80 else text[:79].rstrip() + "…"


def request_caption(items: list[MediaItem]) -> str:
    return next((summary for item in items if (summary := text_summary(item.caption_snippet))), "")


def request_summary(request: MediaRequest, items: list[MediaItem]) -> str:
    caption = request_caption(items)
    original = next((item.original_name for item in items if text_summary(item.original_name)), None)
    fallback = f"媒体组（{len(items)} 个文件）" if request.kind == "group" else (
        "视频" if items[0].media_type == "video" else "图片")
    return text_summary(caption or original) or fallback


def fit_name(stem: str, extension: str, suffix: str = "") -> str:
    stem = safe_name(stem, "file")
    budget = 180 - len((suffix + extension).encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .") or "file"
    return stem + suffix + extension


def planned_name(item: MediaItem, request: MediaRequest, template: str, timezone: str, index: int,
                 *, caption: str | None = None) -> str:
    fallback = ".mp4" if item.media_type == "video" else ".jpg"
    # Extract extension before sanitization/truncation so long Unicode stems cannot lose it.
    original = item.original_name if text_summary(item.original_name) else None
    caption = text_summary(item.caption_snippet if caption is None else caption)
    from_caption = original is None and bool(caption)
    original = original or f"{item.media_type}_{item.id}{fallback}"
    extension = PurePosixPath(original).suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,16}", extension):
        extension = fallback
        stem = original
    else:
        stem = original[:-len(extension)]
    if from_caption:
        # A dotted caption is still title text, never an alleged file extension.
        stem = caption
    date = datetime.fromtimestamp(request.created_at, ZoneInfo(timezone))
    values = {
        "name": safe_name(stem, item.media_type), "id": item.id[:8], "type": item.media_type,
        "date": date.strftime("%Y%m%d"), "time": date.strftime("%H%M%S"),
        "datetime": date.strftime("%Y%m%d_%H%M%S"), "index": str(index),
    }
    fields = [field for _, field, _, _ in Formatter().parse(template)]
    indexed = "index" in fields
    group_caption = from_caption and request.kind == "group"
    # Reserve explicit index and template text before shortening caption values.
    # Otherwise a long Unicode title could push an existing {index} past the cap.
    suffix = f"_{index}" if group_caption and not indexed else ""
    if group_caption and indexed and (occurrences := fields.count("name")):
        overhead = len(render_template(template, values | {"name": ""}).encode("utf-8"))
        budget = max(0, (180 - len(extension.encode("utf-8")) - overhead) // occurrences)
        values["name"] = values["name"].encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .")
    rendered = render_template(template, values)
    # An explicitly supplied matching extension is not duplicated.
    if rendered.lower().endswith(extension.lower()) and (not from_caption or template.lower().endswith(extension.lower())):
        rendered = rendered[:-len(extension)]
    if group_caption and indexed and len(rendered.encode("utf-8")) + len(extension.encode("utf-8")) > 180:
        # Extremely long template literals can overflow even without a caption.
        # Keep the group number visible at the end in that exceptional case.
        suffix = f"_{index}"
    return fit_name(rendered, extension, suffix)


def unique_name(name: str, used: set[str]) -> str:
    path = PurePosixPath(name)
    result, sequence = name, 2
    while result.casefold() in used:
        result = fit_name(path.stem, path.suffix, f"_{sequence}")
        sequence += 1
    used.add(result.casefold())
    return result
