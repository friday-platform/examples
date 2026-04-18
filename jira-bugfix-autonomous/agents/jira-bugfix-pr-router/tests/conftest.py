"""Pytest setup — make the agent package importable and reset SDK state."""

import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from friday_agent_sdk._registry import _reset_registry  # noqa: E402


def pytest_collectstart(collector):
    _reset_registry()
