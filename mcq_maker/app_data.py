"""Centralized per-user data root resolution for MCQ Maker."""
from __future__ import annotations

import os
from pathlib import Path
from platformdirs import user_data_path


# Test/development-only escape hatch. It is inert unless explicitly set and
# is never exposed as an end-user application setting.
TEST_DATA_ROOT_ENV = "MCQ_MAKER_TEST_DATA_ROOT"


def app_data_root(environment=None) -> Path:
    """Return MCQ Maker's app-data directory without redirecting user files."""
    values = os.environ if environment is None else environment
    override = values.get(TEST_DATA_ROOT_ENV)
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_absolute():
            return candidate
    if environment is not None:
        local_app_data = values.get("LOCALAPPDATA")
        return (Path(local_app_data) / "MCQ Maker") if local_app_data else (Path.home() / "MCQ Maker")
    return Path(user_data_path("MCQ Maker", appauthor=False, roaming=False))
