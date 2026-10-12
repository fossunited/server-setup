"""Atomic configuration manager for streams.yaml.

Guarantees:
1. Validates configuration before writing.
2. Writes to a temporary file, flushes, and calls os.fsync().
3. Atomically replaces destination file via os.replace().
4. Supports PyYAML with a lightweight fallback if PyYAML is not present in local test environments.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

from ..models.stream import StreamConfig

logger = logging.getLogger("config_manager")

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class ConfigurationError(Exception):
    """Raised when configuration file is missing, invalid, or corrupted."""


class ConfigManager:
    """Manages loading and atomic persistence of stream configurations."""

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)

    def load_streams(self) -> List[StreamConfig]:
        """Load and parse the streams configuration."""
        if not self.config_path.exists():
            raise ConfigurationError(f"Config file not found: {self.config_path}")

        try:
            content = self.config_path.read_text(encoding="utf-8")
            raw_data = self._parse_yaml(content)
        except Exception as e:
            raise ConfigurationError(f"Failed to read/parse config: {e}") from e

        if not isinstance(raw_data, dict) or "streams" not in raw_data:
            raise ConfigurationError("Config root must contain a 'streams' mapping.")

        stream_list = raw_data.get("streams", [])
        if not isinstance(stream_list, list) or len(stream_list) == 0:
            raise ConfigurationError("'streams' must be a non-empty list.")

        configs: List[StreamConfig] = []
        for item in stream_list:
            try:
                cfg = StreamConfig(**item)
                configs.append(cfg)
            except Exception as e:
                raise ConfigurationError(f"Invalid stream entry: {item}. Error: {e}") from e

        return configs

    def save_streams(self, streams: List[StreamConfig]) -> None:
        """Atomically persist streams to config_path."""
        # 1. Validate all stream configs
        raw_list = [s.model_dump(mode="json") for s in streams]
        data = {"streams": raw_list}

        # 2. Serialize to string
        serialized = self._dump_yaml(data)

        # 3. Write to temporary file in the same directory
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_suffix(".yaml.tmp")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())

            # 4. Atomic rename into destination path
            os.replace(tmp_path, self.config_path)
            logger.info(f"Atomically saved configuration to {self.config_path}")
        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise ConfigurationError(f"Atomic write failed: {e}") from e

    def _parse_yaml(self, text: str) -> Dict[str, Any]:
        if HAS_YAML:
            return yaml.safe_load(text) or {}
        # Fallback minimal YAML/JSON parser for local testing environments without PyYAML
        # Try JSON first
        try:
            return json.loads(text)
        except Exception:
            pass

        # Simple line-based YAML parser for flat stream lists
        return self._simple_yaml_parse(text)

    def _dump_yaml(self, data: Dict[str, Any]) -> str:
        if HAS_YAML:
            return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)

        # Clean readable YAML dumper fallback
        lines = ["streams:"]
        for stream in data.get("streams", []):
            lines.append("  - id: " + json.dumps(str(stream["id"])))
            for k, v in stream.items():
                if k == "id":
                    continue
                if isinstance(v, bool):
                    lines.append(f"    {k}: {'true' if v else 'false'}")
                elif isinstance(v, (int, float)):
                    lines.append(f"    {k}: {v}")
                elif v is None:
                    lines.append(f"    {k}: null")
                else:
                    lines.append(f"    {k}: {json.dumps(str(v))}")
            lines.append("")
        return "\n".join(lines) + "\n"

    def _simple_yaml_parse(self, text: str) -> Dict[str, Any]:
        """Simple YAML parser for streams.yaml when PyYAML is not present."""
        if "streams:" not in text:
            raise ConfigurationError("Missing 'streams:' root element in configuration.")

        streams = []
        current: Dict[str, Any] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line == "streams:":
                continue

            if line.startswith("- "):
                if current:
                    streams.append(current)
                current = {}
                line = line[2:].strip()

            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip()
                # Parse types
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                elif val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                elif val.lower() == "null" or not val:
                    val = None
                else:
                    try:
                        val = int(val)
                    except ValueError:
                        try:
                            val = float(val)
                        except ValueError:
                            pass
                if key == "id" and val is not None:
                    val = str(val)
                current[key] = val

        if current:
            streams.append(current)
        return {"streams": streams}
