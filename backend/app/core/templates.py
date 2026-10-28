"""Small, non-executable plain-text templates shared by validation and rendering."""
from string import Formatter

DEFAULT_CONFIRMATION_TEMPLATE = "📥 确认下载：{title}\n📁 保存到：{destination}\n⏳ 确认有效期：{expires_in_minutes} 分钟\n{files}"
DEFAULT_FILENAME_TEMPLATE = "{name}"
CONFIRMATION_FIELDS = frozenset({"title", "file_count", "total_size", "files", "destination",
                               "expires_in_minutes"})
FILENAME_FIELDS = frozenset({"name", "date", "time", "datetime", "type", "index", "id"})


def validate_template(value: str, *, filename: bool = False) -> str:
    label = "文件名模板" if filename else "确认提示模板"
    if not value.strip() or len(value) > (160 if filename else 2000):
        raise ValueError(f"{label}不能为空，且不可超过{160 if filename else 2000}个字符")
    if any((ord(c) < 32 and (filename or c not in "\n\t")) or ord(c) == 127 for c in value):
        raise ValueError(f"{label}不可包含控制字符")
    if filename and any(c in value for c in '<>:"/\\|?*'):
        raise ValueError("文件名模板不可包含路径分隔符或非法文件名字符")
    allowed = FILENAME_FIELDS if filename else CONFIRMATION_FIELDS
    try:
        for _, field, spec, conversion in Formatter().parse(value):
            if field is not None and (field not in allowed or spec or conversion is not None):
                raise ValueError("invalid field")
        if any(":" in part.split("}", 1)[0] for part in value.replace("{{", "").split("{")[1:]):
            raise ValueError("format specifier")
    except ValueError as exc:
        raise ValueError(f"{label}仅支持列出的变量及双花括号转义，不支持格式表达式") from exc
    return value


def render_template(template: str, values: dict[str, str]) -> str:
    return template.format_map(values)


def render_confirmation(template: str, values: dict[str, str]) -> str:
    text = render_template(template, values)
    def units(value: str) -> int:
        return len(value.encode("utf-16-le")) // 2
    if units(text) <= 3500:
        return text
    # Reserve room for all template literals and each substitution, so a long
    # title cannot push a later file list entirely out of the message.
    parts = list(Formatter().parse(template))
    literals = sum(units(literal) for literal, _, _, _ in parts)
    count = sum(field is not None for _, field, _, _ in parts)
    budget = max(1, (3500 - literals) // max(count, 1))
    bounded = {key: value if units(value) <= budget else (
        value.encode("utf-16-le")[:max(0, budget - 1) * 2].decode("utf-16-le", errors="ignore") + "…"
    ) for key, value in values.items()}
    result = render_template(template, bounded)
    # The template itself may contain 2000 non-BMP characters (>3500 units).
    if units(result) > 3500:
        result = result.encode("utf-16-le")[:6998].decode("utf-16-le", errors="ignore") + "…"
    return result
