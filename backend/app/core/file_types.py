"""Portable attachment classification and safe naming defaults."""
import re
from pathlib import PurePosixPath
from typing import Literal

MediaType = Literal["video", "image", "audio", "voice", "animation", "video_note", "document"]

TYPE_LABELS = {
    "video": "视频", "image": "图片", "audio": "音频", "voice": "语音",
    "animation": "动画", "video_note": "视频留言", "document": "文件",
}
EXTENSION_TYPES: dict[str, MediaType] = {
    **dict.fromkeys((".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".mpeg", ".mpg"), "video"),
    **dict.fromkeys((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".tif", ".tiff"), "image"),
    **dict.fromkeys((".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav", ".wma", ".aiff"), "audio"),
    ".gif": "animation",
}
# Explicit mappings keep MIME hints identical on Linux and Windows. Never derive
# path fragments from an arbitrary MIME subtype or the host's registry.
MIME_EXTENSIONS = {
    "video/mp4": ".mp4", "video/x-matroska": ".mkv", "video/quicktime": ".mov",
    "video/webm": ".webm", "video/x-msvideo": ".avi", "video/mpeg": ".mpeg",
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/bmp": ".bmp",
    "image/heic": ".heic", "image/heif": ".heif", "image/tiff": ".tiff", "image/gif": ".gif",
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac",
    "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/flac": ".flac", "audio/x-flac": ".flac",
    "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/x-ms-wma": ".wma", "audio/aiff": ".aiff",
    "application/pdf": ".pdf", "application/vnd.android.package-archive": ".apk",
    "application/zip": ".zip", "application/x-7z-compressed": ".7z",
    "application/vnd.rar": ".rar", "application/x-rar-compressed": ".rar",
    "application/gzip": ".gz", "application/x-tar": ".tar",
    "application/msword": ".doc", "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/rtf": ".rtf", "text/plain": ".txt", "text/csv": ".csv",
    "application/json": ".json", "application/octet-stream": ".bin",
}
DEFAULT_EXTENSIONS = {
    "video": ".mp4", "image": ".jpg", "audio": ".mp3", "voice": ".ogg",
    "animation": ".mp4", "video_note": ".mp4", "document": ".bin",
}


def original_extension(name: str | None) -> str:
    extension = PurePosixPath(name or "").suffix
    return extension if re.fullmatch(r"\.[A-Za-z0-9]{1,16}", extension) else ""


def mime_extension(mime_type: str | None) -> str:
    return MIME_EXTENSIONS.get((mime_type or "").split(";", 1)[0].strip().lower(), "")


def document_type(name: str | None, mime_type: str | None) -> MediaType:
    # Even an unknown/unsafe suffix takes precedence over a conflicting MIME.
    if PurePosixPath(name or "").suffix:
        return EXTENSION_TYPES.get(original_extension(name).lower(), "document")
    if name and name.strip():
        return "document"  # Named extensionless documents retain an empty suffix.
    return EXTENSION_TYPES.get(mime_extension(mime_type), "document")


def default_extension(media_type: str, mime_type: str | None) -> str:
    hint = mime_extension(mime_type)
    if media_type == "document" or EXTENSION_TYPES.get(hint) == media_type:
        return hint or DEFAULT_EXTENSIONS.get(media_type, ".bin")
    return DEFAULT_EXTENSIONS.get(media_type, ".bin")
