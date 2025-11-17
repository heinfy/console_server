from __future__ import annotations

from pathlib import Path
from typing import Optional

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


def _find_project_root(start: Path) -> Optional[Path]:
    current = start
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return None


def read_pyproject_version() -> Optional[str]:
    if tomllib is None:
        return None
    try:
        root = _find_project_root(Path(__file__).resolve())
        if root is None:
            return None
        pyproject_path = root / "pyproject.toml"
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
        project = data.get("project") or {}
        version = project.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    except Exception:
        # Silently fall back to None if anything goes wrong
        return None
    return None
