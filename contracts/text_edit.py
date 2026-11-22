"""Exact, single-occurrence text replacement for repository tools."""


def replace_unique_text(text, old, new):
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError("Replacement fragments must be strings")
    start = text.find(old)
    if not old or start < 0 or text.find(old, start + 1) >= 0:
        raise ValueError("Patch requires exactly one matching old fragment")
    if new == old:
        raise ValueError("Patch does not change the file")
    return text.replace(old, new, 1)
