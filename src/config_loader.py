from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path("data/papermap.db")

ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def _parse_yaml_scalar(value: str) -> Any:
    text = value.strip()
    if text == "":
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1]

    env_match = ENV_PATTERN.fullmatch(text)
    if env_match:
        return os.getenv(env_match.group(1), "")

    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def load_config(config_path: Path) -> dict[str, Any]:
    """Read a minimal YAML config file with one-level nesting."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    if not config_path.is_file():
        raise ValueError(f"Config path is not a file: {config_path}")

    config: dict[str, Any] = {}
    current_section: str | None = None

    for lineno, raw_line in enumerate(config_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if "\t" in line:
            raise ValueError(f"Unsupported tab indentation at line {lineno}")

        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if indent == 0:
            if ":" not in stripped:
                raise ValueError(f"Invalid config line {lineno}: {raw_line}")
            key, remainder = stripped.split(":", 1)
            key = key.strip()
            value = remainder.strip()
            if not key:
                raise ValueError(f"Empty key at line {lineno}")
            if value == "":
                config[key] = {}
                current_section = key
            else:
                config[key] = _parse_yaml_scalar(value)
                current_section = None
            continue

        if indent != 2:
            raise ValueError(f"Unsupported indentation at line {lineno}: {indent}")
        if current_section is None:
            raise ValueError(f"Nested key without parent section at line {lineno}")
        if ":" not in stripped:
            raise ValueError(f"Invalid nested config line {lineno}: {raw_line}")
        key, remainder = stripped.split(":", 1)
        key = key.strip()
        value = remainder.strip()
        if value == "":
            raise ValueError(f"Nested key value cannot be empty at line {lineno}")
        section = config.get(current_section)
        if not isinstance(section, dict):
            raise ValueError(f"Parent section is not a mapping at line {lineno}")
        section[key] = _parse_yaml_scalar(value)

    return config


def resolve_db_path(db_path_arg: str | None, config: dict[str, Any]) -> Path:
    if db_path_arg:
        return Path(db_path_arg)

    database_cfg = config.get("database", {})
    if isinstance(database_cfg, dict):
        configured_path = database_cfg.get("path")
        if isinstance(configured_path, str) and configured_path.strip():
            return Path(configured_path)
    return DEFAULT_DB_PATH


def config_get(config: dict[str, Any], section: str, key: str, default: Any = None) -> Any:
    section_obj = config.get(section, {})
    if not isinstance(section_obj, dict):
        return default
    return section_obj.get(key, default)
