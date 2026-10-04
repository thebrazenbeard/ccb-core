"""Fail-closed workstation-mediated inter-chat relay primitives.

This module is intentionally transport-independent. Live Windows/Firefox control
is provided by a separate adapter and remains unqualified until real canary
testing proves target selection, rendered readback, and acknowledgement.
"""

from __future__ import annotations

PROTOCOL = "BT2_WORKSTATION_RELAY_V1"
ACK_PROTOCOL = "BT2_WORKSTATION_RELAY_ACK_V1"

__all__ = ["ACK_PROTOCOL", "PROTOCOL"]
