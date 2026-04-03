from __future__ import annotations

from dataclasses import is_dataclass, asdict
from pathlib import Path
import json
import re
from typing import Any


class YamlError(ValueError):
    """Raised when the minimal YAML parser cannot understand a file."""


def load_yaml_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = parse_yaml(text)
    if not isinstance(data, dict):
        raise YamlError(f"{path} must contain a top-level mapping")
    return data


def parse_yaml(text: str) -> Any:
    lines = _prepare_lines(text)
    if not lines:
        return {}
    value, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise YamlError("Unexpected trailing YAML content")
    return value


def dump_yaml(data: Any) -> str:
    return _dump_value(_normalize(data), 0).rstrip() + "\n"


def _prepare_lines(text: str) -> list[tuple[int, str]]:
    prepared: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        line = _strip_comment(raw_line.rstrip())
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2:
            raise YamlError("Only 2-space indentation is supported")
        prepared.append((indent, line.strip()))
    return prepared


def _strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#":
            return line[:index].rstrip()
    return line


def _parse_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    current_indent, current_content = lines[index]
    if current_indent != indent:
        raise YamlError("Inconsistent indentation in YAML block")
    if current_content.startswith("- "):
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise YamlError("Unexpected indentation inside mapping")
        if content.startswith("- "):
            raise YamlError("Mixed mapping and sequence content is not supported")
        if ":" not in content:
            raise YamlError(f"Expected key:value pair, got {content!r}")
        key, remainder = content.split(":", 1)
        key = key.strip()
        remainder = remainder.strip()
        index += 1
        if remainder:
            mapping[key] = _parse_scalar(remainder)
            continue
        if index < len(lines) and lines[index][0] > indent:
            value, index = _parse_block(lines, index, lines[index][0])
            mapping[key] = value
        else:
            mapping[key] = {}
    return mapping, index


def _parse_sequence(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise YamlError("Unexpected indentation inside sequence")
        if not content.startswith("- "):
            break
        remainder = content[2:].strip()
        index += 1
        if remainder:
            items.append(_parse_scalar(remainder))
            continue
        if index < len(lines) and lines[index][0] > indent:
            value, index = _parse_block(lines, index, lines[index][0])
            items.append(value)
        else:
            items.append(None)
    return items, index


def _parse_scalar(value: str) -> Any:
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE", "yes", "Yes", "YES"}:
        return True
    if value in {"false", "False", "FALSE", "no", "No", "NO"}:
        return False
    if value in {"[]", "{}"}:
        return [] if value == "[]" else {}
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        try:
            return json.loads(value) if value.startswith('"') else value[1:-1].replace("\\'", "'")
        except json.JSONDecodeError:
            return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _dump_value(value: Any, indent: int) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return "{}\n"
        chunks: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                chunks.append(f"{prefix}{key}:\n{_dump_value(item, indent + 2)}")
            else:
                chunks.append(f"{prefix}{key}: {_dump_scalar(item)}\n")
        return "".join(chunks)
    if isinstance(value, list):
        if not value:
            return "[]\n"
        chunks = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                nested = _dump_value(item, indent + 2)
                chunks.append(f"{prefix}-\n{nested}")
            else:
                chunks.append(f"{prefix}- {_dump_scalar(item)}\n")
        return "".join(chunks)
    return f"{prefix}{_dump_scalar(value)}\n"


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(char in text for char in [":", "#", "\n", "\r"]) or text.strip() != text:
        return json.dumps(text)
    return text
