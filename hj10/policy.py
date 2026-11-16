"""A deliberately scripted teaching policy. This is NOT an LLM.

It knows this one fixture and a candidate fix in advance. The controller and
tools are real; only the decisions are scripted. Never use its result as a
model success rate, retrieval experiment, or evidence of generalization.
"""

from .agent import Action


FIRST_CONFIG = '''    selected = cli_value or config_value or 5
    if selected <= 0:
        raise ValueError("timeout must be positive")
    return selected
'''

FINAL_CONFIG = '''    from math import isfinite

    selected = cli_value if cli_value is not None else config_value
    if selected is None:
        selected = 5
    if (isinstance(selected, bool) or not isinstance(selected, (int, float))
            or selected <= 0 or (isinstance(selected, float) and not isfinite(selected))):
        raise ValueError("timeout must be a positive finite number")
    return selected
'''


class ScriptedPolicy:
    def __init__(self):
        self._iterator = self._actions()
        self._started = False

    def next_action(self, history):
        try:
            if not self._started:
                self._started = True
                return next(self._iterator)
            return self._iterator.send(history[-1]["observation"])
        except StopIteration:
            return None

    def _actions(self):
        yield Action("map_repository", phase="认识仓库", intent="先找到生产文件、测试和主要函数。")
        yield Action("find_symbol", {"symbol": "resolve_timeout"}, "寻找代码", "查看配置函数的定义和候选引用。")
        yield Action("find_symbol", {"symbol": "build_request"}, "寻找代码", "查看请求构造函数在哪里被调用。")
        for path in ("config.py", "client.py", "cli.py", "tests/test_visible.py"):
            yield Action("read_file", {"path": path}, "阅读上下文", "检查实际源码和可见测试约定。")
        yield Action("plan", {"steps": [
            "在 config.py 合并 CLI、配置和默认值，并校验超时。",
            "在 client.py 使用传入值；检查 cli.py 的跨文件调用。",
            "先运行现有测试，再小步修改、重测和修复边界条件。",
        ]}, "制定计划", "这是预设教学计划；之后的真实模型将在这里依据上下文提出计划。")
        result = yield Action("run_tests", phase="检查起点", intent="记录修改前的真实失败，避免只看到最终成功。")
        if result["status"] == "passed":
            yield Action("finish", {"reason": "起始工作区已经通过可见测试。"}, "结束", "已有测试证据，提交结束请求。")
            return
        yield Action("replace_text", {"path": "config.py", "old": "    return 5\n", "new": FIRST_CONFIG},
                     "第一轮修改", "先演示一个有缺陷的合并方式：or 会混淆 0 与未提供参数。")
        yield Action("replace_text", {"path": "client.py",
                     "old": '    return {"url": url, "timeout": 5}\n',
                     "new": '    return {"url": url, "timeout": timeout}\n'},
                     "第一轮修改", "把最终超时传到请求，完成跨文件参数传递。")
        result = yield Action("run_tests", phase="发现失败", intent="正常路径改善后，测试仍应暴露零值或布尔值错误。")
        if result["status"] == "failed":
            yield Action("read_file", {"path": "config.py"}, "定位原因", "结合刚才的可见失败，重新检查参数合并和校验。")
            yield Action("plan", {"steps": [
                "显式区分 None 与 0，按 CLI、配置、默认值的顺序选择。",
                "仅校验最终选中值；拒绝 bool、字符串和非有限数字。",
                "重新运行可见测试；完成后再由独立评测器验收。",
            ]}, "恢复计划", "脚本预先知道修法；本步展示真实测试反馈如何进入决策接口。")
            yield Action("replace_text", {"path": "config.py", "old": FIRST_CONFIG, "new": FINAL_CONFIG},
                         "失败恢复", "做局部补丁，保留已修复的参数传递。")
            result = yield Action("run_tests", phase="重新验证", intent="对最新文件重新验证，旧的通过记录不能替代本轮测试。")
        yield Action("finish", {"reason": "最新可见测试通过，提交补丁供独立评测。"},
                     "提交结果", "控制器检查完成条件；隐藏测试尚未提供给决策器。")
