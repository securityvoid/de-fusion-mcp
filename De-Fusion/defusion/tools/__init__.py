"""Imports every tool module so that its @action functions register."""
from .. import actions

TOOL_MODULES = (
    "document", "sketch", "model", "assembly", "history", "inspect", "view", "cam", "script", "server",
)

actions.import_tool_modules(__name__, TOOL_MODULES)
