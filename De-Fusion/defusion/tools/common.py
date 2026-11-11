# Helpers shared by tool modules (not a tool module: it is not listed in TOOL_MODULES).


def _try(fn):
    """``fn()`` or ``None`` when the Fusion read fails."""
    try:
        return fn()
    except Exception:
        return None


def _items(coll):
    """Entries of a Fusion collection; an entry whose ``item(i)`` raises (mid-upload) is skipped."""
    out = []
    for i in range(coll.count):
        try:
            item = coll.item(i)
        except Exception:
            continue
        if item is not None:
            out.append(item)
    return out


def _enum_name(enum, value, raw=False):
    """Member name of ``enum`` whose value is ``value``; else the value (``raw``) or its string."""
    for n in dir(enum) if enum is not None else ():
        if not n.startswith("_") and getattr(enum, n) == value:
            return n
    return value if raw else str(value)
