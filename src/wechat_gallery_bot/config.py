import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Config:
    groups: tuple[str, ...]
    data_dir: Path
    pending_seconds: float = 60
    max_image_bytes: int = 20 * 1024 * 1024

    @classmethod
    def load(cls, env_file: Path = Path(".env"), environ: Mapping[str, str] | None = None):
        values: dict[str, str] = {}
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, separator, value = line.partition("=")
                if not separator:
                    raise ValueError("配置行需使用 KEY=VALUE。")
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key.strip()] = value
        values.update(os.environ if environ is None else environ)
        try:
            groups = json.loads(values.get("TIANYI_GROUPS", "[]"))
            timeout = float(values.get("TIANYI_PENDING_SECONDS", "60"))
            limit = int(values.get("TIANYI_MAX_IMAGE_MB", "20"))
        except (ValueError, TypeError) as exc:
            raise ValueError("群名需为 JSON 数组，超时和图片上限需为数字。") from exc
        if not isinstance(groups, list) or any(not isinstance(g, str) or not g.strip() for g in groups):
            raise ValueError("群名需为非空字符串组成的 JSON 数组。")
        names = tuple(g.strip() for g in groups)
        if len(set(names)) != len(names):
            raise ValueError("群名不能重复。")
        if not math.isfinite(timeout) or not 1 <= timeout <= 600 or not 1 <= limit <= 100:
            raise ValueError("超时需为1～600秒，图片上限需为1～100MB。")
        path = Path(values.get("TIANYI_DATA_DIR", "data"))
        if not path.is_absolute():
            path = env_file.resolve().parent / path
        return cls(names, path.resolve(), timeout, limit * 1024 * 1024)
