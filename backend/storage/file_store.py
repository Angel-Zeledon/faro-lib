"""
Atomic JSON file store — the only place that touches the filesystem.

All reads return None if the file doesn't exist.
All writes are atomic: write to a temp file, then rename.
This prevents partial writes from corrupting stored state.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


class FileStore:
    @staticmethod
    def read(path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def write(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp.json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def exists(path: Path) -> bool:
        return path.exists()

    @staticmethod
    def delete(path: Path) -> None:
        if path.exists():
            path.unlink()

    @staticmethod
    def list_json_files(directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        return [p for p in directory.iterdir() if p.suffix == ".json" and p.is_file()]

    @staticmethod
    def list_subdirs(directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        return [p for p in directory.iterdir() if p.is_dir()]

    @staticmethod
    def append_line(path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    @staticmethod
    def read_lines(path: Path, tail: int = 100) -> list[str]:
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return [l.rstrip("\n") for l in lines[-tail:]]


store = FileStore()
