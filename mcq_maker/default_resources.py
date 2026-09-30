"""Resolve bundled defaults and seed independent per-user copies safely."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

from .app_data import app_data_root


# Increment this only when the immutable bundled defaults change. Existing
# per-user copies are intentionally never replaced by a normal startup.
DEFAULT_RESOURCE_VERSION = "1"

_RESOURCE_FILES = {
    "prompt": Path("prompt") / "PROMPT(MCQ-MAKER).txt",
    "reference": Path("reference") / "refrence.txt",
    "template": Path("templates") / "standard_exam.html",
}


def bundled_resource_root() -> Path:
    """Return the package resource directory in source and frozen builds."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "mcq_maker" / "resources"
    return Path(__file__).resolve().parent / "resources"


def _atomic_write(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        # Concurrent first-use seeding can race on Windows. A directory that
        # appeared during the attempt is safe; anything else is not.
        if not path.parent.is_dir():
            raise
    descriptor, temporary = tempfile.mkstemp(prefix=".seed-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class DefaultResourceStore:
    """Immutable package defaults with user-owned, versioned working copies."""

    def __init__(self, root: Path | None = None, *, package_root: Path | None = None):
        self.root = Path(root) if root is not None else app_data_root()
        self.package_root = Path(package_root) if package_root is not None else bundled_resource_root()

    @property
    def seeded_root(self) -> Path:
        return self.root / "defaults" / DEFAULT_RESOURCE_VERSION

    def bundled_path(self, kind: str) -> Path:
        try:
            relative = _RESOURCE_FILES[kind]
        except KeyError as exc:
            raise ValueError(f"Unknown default resource: {kind}") from exc
        path = self.package_root / "defaults" / relative
        if not path.is_file():
            raise FileNotFoundError(f"Bundled default resource is missing: {path.name}")
        return path

    def seeded_path(self, kind: str) -> Path:
        return self.seeded_root / _RESOURCE_FILES[kind]

    def _legacy_seeded_path(self, kind: str) -> Path:
        """Read an early Batch A layout without modifying its user copy."""
        return self.seeded_root / "defaults" / _RESOURCE_FILES[kind]

    def ensure_seeded(self) -> None:
        """Create only absent user copies; never overwrite user edits."""
        for kind in _RESOURCE_FILES:
            target = self.seeded_path(kind)
            if target.is_file():
                continue
            legacy = self._legacy_seeded_path(kind)
            source = legacy if legacy.is_file() else self.bundled_path(kind)
            _atomic_write(target, source.read_bytes())

    def default_path(self, kind: str) -> Path:
        self.ensure_seeded()
        return self.seeded_path(kind)

    def resolve_existing_or_default(self, value: str | Path | None, kind: str) -> Path:
        """Keep a usable legacy/custom selection, otherwise provide a safe default."""
        if value:
            candidate = Path(value)
            if candidate.is_file():
                return candidate
        return self.default_path(kind)
