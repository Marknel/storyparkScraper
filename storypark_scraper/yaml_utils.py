from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
import yaml


class YamlError(ValueError):
    """Raised when YAML parsing fails."""


def load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise YamlError(f"Failed to read {path}: {exc}") from exc
    data = parse_yaml(text)
    if not isinstance(data, dict):
        raise YamlError(f"{path} must contain a top-level mapping")
    return data


def parse_yaml(text: str) -> Any:
    if not text.strip():
        return {}
    try:
        data = yaml.safe_load(text)
        return {} if data is None else data
    except yaml.YAMLError as exc:
        raise YamlError(f"YAML parsing error: {exc}") from exc


def dump_yaml(data: Any) -> str:
    normalized = _normalize(data)
    return yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True)


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value
