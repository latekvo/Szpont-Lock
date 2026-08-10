"""Shared test setup: put the applet package on the path and keep every test's Qt /
config / storage side effects inside the pytest tmp area."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# linux/ (the parent of this tests/ dir) holds the szpontlock_app package.
_LINUX_DIR = Path(__file__).resolve().parents[1]
if str(_LINUX_DIR) not in sys.path:
    sys.path.insert(0, str(_LINUX_DIR))

# Qt widgets/gui import cleanly with no display under the offscreen platform.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from szpontlock_app.secretstore import SecretStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    """Point SecretStore at a scratch directory for the duration of a test."""
    SecretStore._reset_for_testing(tmp_path)
    return SecretStore
