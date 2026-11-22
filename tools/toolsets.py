"""Toolset registry: which tools are exposed in a run.

A toolset only chooses tool names; the environment is selected by the runner.
Prompt tables, report labels and dispatch validation all derive from this
single mapping, so a tool cannot be advertised without a spec.
"""

from tools.specs import TOOL_SPECS


TOOLSETS = {
    "toy-v2": {
        "tools": (
            "list_files",
            "read_lines",
            "apply_patch",
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


def specs_for(name=DEFAULT_TOOLSET):
    return {tool: TOOL_SPECS[tool] for tool in tool_names(name)}


def tool_table(name=DEFAULT_TOOLSET):
    """Render only the enabled tools for a model prompt."""
    lines = []
    for tool, spec in specs_for(name).items():
        args = ", ".join(f"{key}: {kind}" for key, kind in spec["args"].items()) or "无"
        lines.append(f"- {tool}({args})：{spec['doc']}")
    return "\n".join(lines)


def tool_labels(name=DEFAULT_TOOLSET):
    """Report labels for the enabled tools only."""
    return {tool: TOOL_SPECS[tool]["label"] for tool in tool_names(name)}
