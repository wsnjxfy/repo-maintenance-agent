"""JSON extraction and argument normalization for model tool actions."""

import json


def strip_fence(text):
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        _, _, body = stripped.partition("\n")
        stripped = body if body else stripped[3:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def extract_json(text):
    """Accept bare JSON, fenced JSON, JSON in prose, and multiple objects.

    Returns the first value and the number of additional JSON values found.
    """
    stripped = strip_fence(text)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        value = None
    if value is not None:
        if isinstance(value, list):
            items = [item for item in value if isinstance(item, dict)]
            if not items:
                return value, 0
            return items[0], max(len(items) - 1, 0)
        return value, 0
    decoder = json.JSONDecoder()
    first, extra, index = None, 0, 0
    while index < len(stripped):
        start = stripped.find("{", index)
        if start == -1:
            break
        try:
            value, end = decoder.raw_decode(stripped, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if first is None:
            first = value
        else:
            extra += 1
        index = end
    if first is None:
        raise json.JSONDecodeError("No JSON object found", stripped, 0)
    return first, extra


def normalize_args(data):
    """Accept args, a JSON string in arguments, or top-level parameters."""
    args = data.get("args")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = None
    if not isinstance(args, dict) or not args:
        arguments = data.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = None
        if isinstance(arguments, dict) and arguments:
            args = arguments
    if not isinstance(args, dict) or not args:
        loose = {key: value for key, value in data.items()
                 if key not in {"tool", "args", "arguments"}}
        if loose:
            args = loose
    return args if isinstance(args, dict) else {}
