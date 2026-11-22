"""Tool catalog: every tool the model may call, across all toolsets.

Only *specifications* live here (name, arguments, description, labels, phase,
audit flag, handler key). Actual execution lives in ``tools/handlers.py`` for
the repo family and in ``runtime/backends/toy.py`` for the teaching fixture;
which subset is exposed for a run is decided by ``tools/toolsets.py``.
"""

TOOL_SPECS = {
    # --- toy toolset (fixture-sized teaching workspace) ---------------------
    "map_repository": {
        "args": {},
        "doc": "用 AST 列出可读文件的函数与类定义。",
        "label": "建立仓库地图",
        "phase": "认识仓库",
        "mutating": False,
        "executes_code": False,
        "handler": "map_repository",
    },
    "find_symbol": {
        "args": {"symbol": "identifier"},
        "doc": "查找符号的定义位置与文本候选引用。",
        "label": "检索符号",
        "phase": "寻找代码",
        "mutating": False,
        "executes_code": False,
        "handler": "find_symbol",
    },
    "read_file": {
        "args": {"path": "str"},
        "doc": "读取允许范围内的文件全文。",
        "label": "阅读代码",
        "phase": "阅读上下文",
        "mutating": False,
        "executes_code": False,
        "handler": "read_file",
    },
    "plan": {
        "args": {"steps": "list[str]"},
        "doc": "记录修改计划的文本步骤。",
        "label": "规划修改",
        "phase": "制定计划",
        "mutating": False,
        "executes_code": False,
        "handler": "plan",
    },
    "replace_text": {
        "args": {"path": "str", "old": "str", "new": "str"},
        "doc": "在文件中做唯一片段替换；old 必须恰好出现一次。",
        "label": "应用增量补丁",
        "phase": "修改代码",
        "mutating": True,
        "executes_code": False,
        "handler": "replace_text",
    },
    "run_tests": {
        "args": {},
        "doc": "在独立进程中运行可见测试，返回结构化结果。",
        "label": "运行可见测试",
        "phase": "验证",
        "mutating": False,
        "executes_code": True,
        "handler": "run_tests",
    },
    # --- repo toolset (real repository workspaces) --------------------------
    "list_files": {
        "args": {"path": "str", "limit": "int"},
        "doc": "列出目录下的文件（相对路径，最多 limit 个）。",
        "label": "列出文件",
        "phase": "列出文件",
        "mutating": False,
        "executes_code": False,
        "handler": "list_files",
    },
    "search_text": {
        "args": {"query": "str", "path": "str", "limit": "int", "case_sensitive": "bool"},
        "doc": "在文件或目录中做字面文本搜索，返回文件名与行号。",
        "label": "搜索文本",
        "phase": "搜索文本",
        "mutating": False,
        "executes_code": False,
        "handler": "search_text",
    },
    "read_lines": {
        "args": {"path": "str", "start": "int", "end": "int"},
        "doc": "读取文件的行区间（start/end 为 1 起始，含端点）。",
        "label": "读取行区间",
        "phase": "阅读行区间",
        "mutating": False,
        "executes_code": False,
        "handler": "read_lines",
    },
    "apply_patch": {
        "args": {"patch": "str"},
        "doc": "套用 unified diff；测试与配置文件只读，失败时整批还原。",
        "label": "应用补丁",
        "phase": "应用补丁",
        "mutating": True,
        "executes_code": False,
        "handler": "apply_patch",
    },
    "run_visible_tests": {
        "args": {"paths": "list[str]", "timeout_seconds": "float"},
        "doc": "运行指定 pytest 目标（最多 30 个），返回通过/失败统计。",
        "label": "运行可见测试",
        "phase": "验证",
        "mutating": False,
        "executes_code": True,
        "handler": "run_visible_tests",
    },
    # --- shared -------------------------------------------------------------
    "finish": {
        "args": {"reason": "str"},
        "doc": "提交完成请求；由 backend 决定接受或导出补丁，判定交给评测。",
        "label": "请求结束",
        "phase": "提交结果",
        "mutating": False,
        "executes_code": False,
        "handler": "finish",
    },
}
