from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"failed to parse JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"scenario file must contain a JSON object: {path}")
    return data


def resolve_entry_path(base_dir: Path, raw_path: str, *, strict: bool = True) -> Path:
    resolved = Path(raw_path)
    if not resolved.is_absolute():
        resolved = base_dir / resolved
    try:
        return resolved.resolve(strict=strict)
    except FileNotFoundError as exc:
        raise SystemExit(f"failed to resolve scenario path {resolved}") from exc


def absolute_from_cwd(path: Path) -> Path:
    if path.is_absolute():
        return path
    try:
        return Path.cwd() / path
    except OSError as exc:
        raise SystemExit("failed to get current directory") from exc
