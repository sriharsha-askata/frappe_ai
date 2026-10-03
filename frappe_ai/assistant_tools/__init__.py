"""Assistant Core contributions and the public decorated-tool API."""

from frappe_ai.assistant_tools.registry import plugin, tool
from frappe_ai.assistant_tools.sync import sync_decorated_tools

__all__ = ["plugin", "sync_decorated_tools", "tool"]
