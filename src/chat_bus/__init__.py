"""Durable coordination primitives for the Chat Bus proof of concept.

This package re-exports the public types from chat_bus.ledger so callers can
import them directly from the package root (for example: ``from chat_bus import Ledger``).
"""

from importlib.metadata import PackageNotFoundError, version

try:
    # Read the installed package version when available; fall back when running
    # from a source tree (not installed).
    __version__ = version("chat-communication-bus")
except PackageNotFoundError:
    __version__ = "0+unknown"

from .ledger import Ledger, LedgerError

__all__ = ["Ledger", "LedgerError", "__version__"]
