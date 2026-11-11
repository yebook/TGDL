"""Plan safe basenames once, independently of subsequent configuration edits."""
import unicodedata
from datetime import datetime
from string import Formatter
from zoneinfo import ZoneInfo

from app.core.file_types import TYPE_LABELS, default_extension, original_extension
from app.core.templates import DEFAULT_MESSAGE_TITLE_LENGTH, MAX_MESSAGE_TITLE_LENGTH, render_template
from app.db.models import MediaItem, MediaRequest
from app.storage.files import safe_name


def text_summary(value: str | None, limit: int = MAX_MESSAGE_TITLE_LENGTH, *, first_line: bool = False) -> str:
    """Retain a bounded, plain single-line message summary without hidden controls."""
    if first_line:
        value = next(iter((value or "").splitlines()), "")
    text = " ".join("".join(" " if unicodedata.category(char).startswith("C") else char
                            for char in (value or "")).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def request_caption(items: list[MediaItem], title_length: int = DEFAULT_MESSAGE_TITLE_LENGTH,
                    *, first_line: bool = True) -> str:
    return next((summary for item in items
                 if (summary := text_summary(
                     item.caption_first_line if first_line and item.caption_first_line is not None else item.caption_snippet,
                     title_length, first_line=first_line))), "")


def request_summary(request: MediaRequest, items: list[MediaItem],
                    title_length: int = DEFAULT_MESSAGE_TITLE_LENGTH, *, first_line: bool = True) -> str:
    caption = request_caption(items, title_length, first_line=first_line)
    original = next((item.original_name for item in items if text_summary(item.original_name)), None)
    fallback = f"文件组（{len(items)} 个文件）" if request.kind == "group" else (
        TYPE_LABELS.get(items[0].media_type, "文件"))
    return text_summary(request.name_override) or text_summary(caption or original, title_length, first_line=first_line) or fallback


def fit_name(stem: str, extension: str, suffix: str = "") -> str:
    stem = safe_name(stem, "file")
    budget = 180 - len((suffix + extension).encode("utf-8"))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .") or "file"
    return stem + suffix + extension


def planned_name(item: MediaItem, request: MediaRequest, template: str, timezone: str, index: int,
                 *, caption: str | None = None, title_length: int = DEFAULT_MESSAGE_TITLE_LENGTH,
                 first_line: bool = True) -> str:
    fallback = default_extension(item.media_type, item.mime_type)
    # Extract extension before sanitization/truncation so long Unicode stems cannot lose it.
    original = item.original_name if text_summary(item.original_name) else None
    if caption is None:
        caption = item.caption_first_line if first_line and item.caption_first_line is not None else item.caption_snippet
    caption = text_summary(caption, title_length, first_line=first_line)
    override = text_summary(request.name_override)
    from_caption = bool(override) or (original is None and bool(caption))
    if original is None:
        stem, extension = item.media_type, fallback
    else:
        extension = original_extension(original)
        stem = original[:-len(extension)] if extension else original
        # Keep the released image/video fallback; generic and newly supported
        # named attachments keep their actual extensionless name.
        if not extension and item.media_type in {"image", "video"}:
            extension = fallback
    if from_caption:
        # A dotted caption is still title text, never an alleged file extension.
        stem = override or caption
    date = datetime.fromtimestamp(request.created_at, ZoneInfo(timezone))
    values = {
        "name": safe_name(stem, item.media_type), "id": item.id[:8], "type": item.media_type,
        "date": date.strftime("%Y%m%d"), "time": date.strftime("%H%M%S"),
        "datetime": date.strftime("%Y%m%d_%H%M%S"), "index": str(index),
    }
    fields = [field for _, field, _, _ in Formatter().parse(template)]
    indexed = "index" in fields
    # Explicit sequence variables remain opt-in and survive long title truncation.
    suffix = ""
    if indexed and (occurrences := fields.count("name")):
        overhead = len(render_template(template, values | {"name": ""}).encode("utf-8"))
        budget = max(0, (180 - len(extension.encode("utf-8")) - overhead) // occurrences)
        values["name"] = values["name"].encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip(" .")
    rendered = render_template(template, values)
    # An explicitly supplied matching extension is not duplicated.
    if extension and rendered.lower().endswith(extension.lower()) and (not from_caption or template.lower().endswith(extension.lower())):
        rendered = rendered[:-len(extension)]
    if indexed and len(rendered.encode("utf-8")) + len(extension.encode("utf-8")) > 180:
        # Extremely long template literals can overflow even without a caption.
        # Keep the group number visible at the end in that exceptional case.
        suffix = f"_{index}"
    return fit_name(rendered, extension, suffix)


def unique_name(name: str, used: set[str]) -> str:
    # Apply the same safe-suffix rule as planning. An arbitrary document suffix
    # can consume the entire byte budget, so it must remain part of the stem.
    extension = original_extension(name)
    stem = name[:-len(extension)] if extension else name
    result, sequence = name, 2
    while result.casefold() in used:
        result = fit_name(stem, extension, f"_{sequence}")
        sequence += 1
    used.add(result.casefold())
    return result
