"""Toolset registry: which tools are exposed in a run.

A toolset only chooses tool names; the environment is selected by the runner.
Prompt tables, report labels and dispatch validation all derive from this
single mapping, so a tool cannot be advertised without a spec.
"""

from tools.handlers import HANDLERS
from tools.specs import TOOL_SPECS


TOOLSETS = {
    "toy-v2": {
        "tools": (
            "list_files",
            "read_lines",
            "replace_text",
            "run_visible_tests",
            "finish",
        ),
    },
    "repo-v1": {
        "tools": (
            "list_files",
            "search_text",
            "read_lines",
            "apply_patch",
            "run_visible_tests",
            "finish",
        ),
    },
    "repo-v2": {
        # repo-v1 plus navigation (regex / AST map / symbol lookup), finer edits
        # (create_file / edit_lines), recovery (revert_file) and self-review
        # (show_diff). A richer surface for comparing agent designs.
        "tools": (
            "list_files",
            "search_text",
            "search_regex",
            "read_lines",
            "map_repository",
            "find_symbol",
            "apply_patch",
            "replace_text",
            "create_file",
            "edit_lines",
            "revert_file",
            "show_diff",
            "run_visible_tests",
            "finish",
        ),
    },
}

# No repository subprocesses run during solve for tasks with local holdout material.
TOOLSETS["repo-edit"] = {
    "tools": tuple(name for name in TOOLSETS["repo-v2"]["tools"]
                   if not TOOL_SPECS[name]["executes_code"]),
}

DEFAULT_TOOLSET = "toy-v2"


def get_toolset(name=DEFAULT_TOOLSET):
    """Return the validated toolset entry, or raise a clear error."""
    try:
        entry = TOOLSETS[name]
    except KeyError:
        raise ValueError(
            "Unknown toolset: {!r}. Available: {}".format(
                name, ", ".join(sorted(TOOLSETS))
            )
        ) from None
    missing = [tool for tool in entry["tools"] if tool not in TOOL_SPECS]
    if missing:
        raise ValueError(
            "Toolset {!r} references undefined tools: {}".format(name, ", ".join(missing))
        )
    return entry


def tool_names(name=DEFAULT_TOOLSET):
    return list(get_toolset(name)["tools"])


def available_tools():
    """Names of every spec that can actually be dispatched (has a handler).

    ``TOOL_SPECS`` still carries a few historical entries with no handler in
    ``HANDLERS``; exposing one would raise ``KeyError`` at dispatch. A UI or a
    ``--tools`` list must choose from this set only.
    """
    return [name for name, spec in TOOL_SPECS.items() if spec["handler"] in HANDLERS]


def validate_tool_names(names):
    """Validate an explicit tool-name list and return it deduplicated.

    Rejects an empty list, unknown names, duplicates and specs without a
    handler. This is the gate between a caller-chosen subset (checkboxes,
    ``--tools``) and the dispatch layer.
    """
    ordered = list(names)
    if not ordered:
        raise ValueError("Tool list must be nonempty")
    if len(set(ordered)) != len(ordered):
        raise ValueError("Tool list contains duplicate names")
    runnable = set(available_tools())
    missing = [name for name in ordered if name not in TOOL_SPECS]
    if missing:
        raise ValueError("Unknown tools: " + ", ".join(missing))
    no_handler = [name for name in ordered if name not in runnable]
    if no_handler:
        raise ValueError("Tools without a handler: " + ", ".join(no_handler))
    return ordered


def specs_for(name=DEFAULT_TOOLSET):
    return specs_for_names(tool_names(name))


def specs_for_names(names):
    return {tool: TOOL_SPECS[tool] for tool in names}


def tool_table(name=DEFAULT_TOOLSET):
    """Render only the enabled tools for a model prompt."""
    return tool_table_for(tool_names(name))


def tool_table_for(names):
    """Render an explicit list of tools for a model prompt."""
    lines = []
    for tool in names:
        spec = TOOL_SPECS[tool]
        args = ", ".join(f"{key}: {kind}" for key, kind in spec["args"].items()) or "无"
        lines.append(f"- {tool}({args})：{spec['doc']}")
    return "\n".join(lines)


def tool_labels(name=DEFAULT_TOOLSET):
    """Report labels for the enabled tools only."""
    return tool_labels_for(tool_names(name))


def tool_labels_for(names):
    """Report labels for an explicit list of tools."""
    return {tool: TOOL_SPECS[tool]["label"] for tool in names}


def tool_versions(names=None):
    """Snapshot per-tool implementation versions for a run."""
    selected = list(names) if names is not None else list(TOOL_SPECS)
    return {name: TOOL_SPECS[name]["version"] for name in selected}
