"""Profiles: named sets of allowed actions (design § 6.2-6.4)."""
import re
from fnmatch import fnmatchcase
from urllib.parse import parse_qs

from .actions import ACCESS, TOOLS

BUILTIN = {
    "full": ["all"],
    "readonly": ["-write", "-destructive"],
    "3dprinting": ["all", "-cam", "-model.sheet_*", "-script"],
    "metalworking": ["all", "-script"],
}

_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidProfile(Exception):
    """A bad profile item (maps to INVALID_PROFILE, a JSON-RPC-level error, § 6.4)."""

    def __init__(self, item, reason):
        self.item, self.reason = item, reason
        super().__init__("invalid profile item '%s': %s; valid selectors are in design § 6.3" % (item, reason))


def _is_builtin_selector(selector):
    return selector == "all" or selector in ACCESS or selector in TOOLS or "." in selector


def expand(items, profiles, _stack=()):
    """Splice profile names recursively; returns ``[(negated, selector)]``.

    Raises InvalidProfile for ``-<profile>`` and for a cycle. Other selectors are left as written."""
    out = []
    for item in items:
        neg = item.startswith("-")
        selector = item[1:] if neg else item
        if not selector.strip():
            raise InvalidProfile(item, "blank item")
        if not _is_builtin_selector(selector) and selector in profiles:
            if neg:
                raise InvalidProfile(item, "a profile cannot be removed with '-'")
            if selector in _stack:
                raise InvalidProfile(item, "profile cycle %s" % " -> ".join(_stack + (selector,)))
            out.extend(expand(profiles[selector], profiles, _stack + (selector,)))
        else:
            out.append((neg, selector))
    return out


def merge(builtin, user):
    """Built-in profiles overlaid with the user's; checks names, ``-<profile>`` and cycles."""
    for name in user:
        if not _NAME.match(name):
            raise InvalidProfile(name, "profile names use letters, digits, '_' and '-'")
        if name == "all" or name in ACCESS or name in TOOLS:
            raise InvalidProfile(name, "a profile cannot be named like 'all', an access class or a tool")
    merged = {**builtin, **user}
    for name, items in merged.items():
        expand(items, merged, (name,))
    return merged


def resolve(items, profiles, registry):
    """The set of ``(tool, action)`` an item list allows."""
    expanded = expand(items, profiles)
    if not expanded:
        raise InvalidProfile("", "no items")
    actions = list(registry)
    allowed = set() if not expanded[0][0] else {(a.tool, a.name) for a in actions}
    for neg, selector in expanded:
        item = ("-" if neg else "") + selector
        matched = _match(selector, actions)
        if not matched:
            raise InvalidProfile(item, "matches no action")
        allowed = allowed - matched if neg else allowed | matched
    return allowed


def _match(selector, actions):
    if selector == "all":
        return {(a.tool, a.name) for a in actions}
    if selector in ACCESS:
        return {(a.tool, a.name) for a in actions if a.access == selector}
    if selector in TOOLS:
        return {(a.tool, a.name) for a in actions if a.tool == selector}
    tool, dot, pattern = selector.partition(".")
    if dot and tool in TOOLS and pattern:
        return {(a.tool, a.name) for a in actions if a.tool == tool and fnmatchcase(a.name, pattern)}
    raise InvalidProfile(selector, "not 'all', an access class, a tool, 'tool.pattern' or a profile name")


def parse_query(qs):
    """Items from a raw query string's ``profile`` value, or None when there is no ``profile`` key."""
    values = parse_qs(qs or "", keep_blank_values=True).get("profile")
    if values is None:
        return None
    if len(values) > 1:
        raise InvalidProfile("profile=" + "&profile=".join(values), "profile may be given only once")
    if not values[0].strip():
        raise InvalidProfile("", "profile is empty")
    items = [i.strip() for i in values[0].split(",")]
    for i in items:
        if not i:
            raise InvalidProfile(values[0], "blank item")
    return items
