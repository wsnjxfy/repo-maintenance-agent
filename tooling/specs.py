"""Tool catalog: the single source of truth for what the model may call.

Only *specifications* live here (name, arguments, description, label). Actual
execution lives in ``tooling.backends``; dispatch, prompt tables, report labels
and guard tests all derive from this catalog.
"""

TOOL_SPECS = {
    "map_repository": {
        "args": {},
        "doc": "用 AST 列出可读文件的函数与类定义。",
        "label": "建立仓库地图",
        "mutating": False,
    },
    "find_symbol": {
        "args": {"symbol": "identifier"},
        "doc": "查找符号的定义位置与文本候选引用。",
        "label": "检索符号",
        "mutating": False,
    },
    "read_file": {
        "args": {"path": "str"},
        "doc": "读取允许范围内的文件全文。",
        "label": "阅读代码",
        "mutating": False,
    },
    "plan": {
        "args": {"steps": "list[str]"},
        "doc": "记录修改计划的文本步骤。",
        "label": "规划修改",
        "mutating": False,
    },
    "replace_text": {
        "args": {"path": "str", "old": "str", "new": "str"},
        "doc": "在文件中做唯一片段替换；old 必须恰好出现一次。",
        "label": "应用增量补丁",
        "mutating": True,
    },
    "run_tests": {
        "args": {},
        "doc": "在独立进程中运行可见测试，返回结构化结果。",
        "label": "运行可见测试",
        "mutating": False,
    },
    "finish": {
        "args": {"reason": "str"},
        "doc": "提交完成请求；要求最新工作区已有通过的测试。",
        "label": "请求结束",
        "mutating": False,
    },
}


def tool_labels():
    """Report labels derived from the catalog, so a rename cannot go stale."""
    return {name: spec["label"] for name, spec in TOOL_SPECS.items()}


def tool_table():
    """Render the catalog for a model prompt. Never hand-write this table."""
    lines = []
    for name, spec in TOOL_SPECS.items():
        args = ", ".join(f"{key}: {kind}" for key, kind in spec["args"].items()) or "无"
        lines.append(f"- {name}({args})：{spec['doc']}")
    return "\n".join(lines)
