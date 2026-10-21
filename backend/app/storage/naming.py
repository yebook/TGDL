"""Plan safe basenames once, independently of subsequent configuration edits."""
import re
import unicodedata
from datetime import datetime
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from app.core.templates import render_template
from app.db.models import MediaItem, MediaRequest
from app.storage.files import safe_name


def text_summary(value: str | None) -> str:
    """Retain a bounded, plain single-line message summary without hidden controls."""
    text = " ".join("".join(" " if unicodedata.category(char).startswith("C") else char
                            for char in (value or "")).split())
    return text if len(text) <= 80 else text[:79].rstrip() + "…"


def request_summary(request: MediaRequest, items: list[MediaItem]) -> str:
    caption = next((item.caption_snippet for item in items if item.caption_snippet), None)
    original = next((item.original_name for item in items if text_summary(item.original_name)), None)
    fallback = f"媒体组（{len(items)} 个文件）" if request.kind == "group" else (
        "视频" if items[0].media_type == "video" else "图片")
    return text_summary(caption or original) or fallback


def fit_name(stem: str, extension: str, suffix: str = "") -> str:
    stem = safe_name(stem, "file")
    budget = 180 - len((suffix + extension).encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .") or "file"
    return stem + suffix + extension


def planned_name(item: MediaItem, request: MediaRequest, template: str, timezone: str, index: int) -> str:
    fallback = ".mp4" if item.media_type == "video" else ".jpg"
    # Extract extension before sanitization/truncation so long Unicode stems cannot lose it.
    original = item.original_name or f"{item.media_type}_{item.id}{fallback}"
    extension = PurePosixPath(original).suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,16}", extension):
        extension = fallback
        stem = original
    else:
        stem = original[:-len(extension)]
    date = datetime.fromtimestamp(request.created_at, ZoneInfo(timezone))
    rendered = render_template(template, {
        "name": safe_name(stem, item.media_type), "id": item.id[:8], "type": item.media_type,
        "date": date.strftime("%Y%m%d"), "time": date.strftime("%H%M%S"),
        "datetime": date.strftime("%Y%m%d_%H%M%S"), "index": str(index),
    })
    # An explicitly supplied matching extension is not duplicated.
    if rendered.lower().endswith(extension.lower()):
        rendered = rendered[:-len(extension)]
    return fit_name(rendered, extension)


def unique_name(name: str, used: set[str]) -> str:
    path = PurePosixPath(name)
    result, sequence = name, 2
    while result.casefold() in used:
        result = fit_name(path.stem, path.suffix, f"_{sequence}")
        sequence += 1
    used.add(result.casefold())
    return result
