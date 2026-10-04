"""Windows/Firefox target adapter for the workstation relay.

Importing this module is cross-platform. Live UI Automation support is loaded
only when a concrete driver is constructed on Windows.
"""

from __future__ import annotations

ADAPTER = "WINDOWS_FIREFOX_UIA_V1"

__all__ = ["ADAPTER"]
