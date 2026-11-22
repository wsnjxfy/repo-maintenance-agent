"""Git patch header parsing shared by tools and the independent evaluator.

Only regular-file patches with ``a/``/``b/`` prefixes are accepted; renames,
copies, quoted paths and non-regular modes are rejected. Callers keep their own
safety checks (workspace boundary, protected paths, generated metadata).
"""

from __future__ import annotations


def parse_patch_paths(text: str, *, strict: bool = False) -> list[str]:
    """Return relative paths touched by a Git unified diff.

    ``strict`` reproduces the evaluator's stricter scanning: it requires at
    least one ``diff --git`` header and only inspects mode/--- lines while
    inside a file header. The tool side keeps the permissive scan it had.
    """
    paths: set[str] = set()
    saw_diff = False
    in_header = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            tokens = line.split()
            if len(tokens) != 4:
                raise ValueError("Quoted or whitespace patch paths are unsupported")
            if strict and any('"' in token for token in tokens[2:]):
                raise ValueError("Quoted or whitespace patch paths are unsupported")
            paths.update(tokens[2:])
            saw_diff = True
            in_header = True
            continue
        if line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            raise ValueError("Rename/copy patches are unsupported")
        if line.startswith(("old mode ", "new mode ", "new file mode ", "deleted file mode ")):
            if strict and not in_header:
                continue
            if line.split()[-1] not in {"100644", "100755"}:
                raise ValueError("Only regular-file patches are supported")
            continue
        if line.startswith(("--- ", "+++ ")):
            if strict and not in_header:
                continue
            token = line[4:].split("\t", 1)[0]
            if token != "/dev/null":
                paths.add(token)
            continue
        if line.startswith("@@"):
            in_header = False

    if strict and not saw_diff:
        raise ValueError("Expected a Git unified patch with file headers")
    if not paths:
        raise ValueError("Patch contains no file headers")

    relative: set[str] = set()
    for value in paths:
        if not value.startswith(("a/", "b/")):
            raise ValueError("Patch paths must use a/ and b/ prefixes")
        relative.add(value[2:].replace("\\", "/"))
    return sorted(relative)
