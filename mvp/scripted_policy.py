"""Scripted policy for the v2 toy demo (same fixture, real tools).

It knows this fixture and the fix in advance. The loop, Session and
LocalEnvironment are real; only the decisions are scripted.
"""

from agent_core.agent import Action


FINAL_CONFIG = '''    from math import isfinite

    selected = cli_value if cli_value is not None else config_value
    if selected is None:
        selected = 5
    if (isinstance(selected, bool) or not isinstance(selected, (int, float))
            or selected <= 0 or (isinstance(selected, float) and not isfinite(selected))):
        raise ValueError("timeout must be a positive finite number")
    return selected
'''

PATCH = '''diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -4,2 +4,10 @@
 def resolve_timeout(config_value=None, cli_value=None):
-    return 5
+    from math import isfinite
+
+    selected = cli_value if cli_value is not None else config_value
+    if selected is None:
+        selected = 5
+    if (isinstance(selected, bool) or not isinstance(selected, (int, float))
+            or selected <= 0 or (isinstance(selected, float) and not isfinite(selected))):
+        raise ValueError("timeout must be a positive finite number")
+    return selected
diff --git a/client.py b/client.py
--- a/client.py
+++ b/client.py
@@ -4,2 +4,2 @@
 def build_request(url, timeout=5):
-    return {"url": url, "timeout": 5}
+    return {"url": url, "timeout": timeout}
'''


class ToyV2Policy:
    def __init__(self):
        self.actions = [
            ("list_files", {"path": ".", "limit": 20}, "列出文件", "先看工作区有哪些文件。"),
            ("read_lines", {"path": "config.py", "start": 1, "end": 10}, "阅读行区间", "查看配置函数。"),
            ("read_lines", {"path": "client.py", "start": 1, "end": 10}, "阅读行区间", "查看请求构造。"),
            ("read_lines", {"path": "cli.py", "start": 1, "end": 10}, "阅读行区间", "查看跨文件调用。"),
            ("read_lines", {"path": "tests/test_visible.py", "start": 1, "end": 43},
             "阅读行区间", "检查可见测试约定。"),
            ("run_visible_tests", {"paths": ["tests/test_visible.py"]}, "验证", "记录修改前的真实失败。"),
            ("apply_patch", {"patch": PATCH}, "应用补丁", "一次修改 config.py 与 client.py。"),
            ("run_visible_tests", {"paths": ["tests/test_visible.py"]}, "验证", "对最新文件重跑可见测试。"),
            ("finish", {"reason": "最新可见测试通过，提交补丁供独立评测。"}, "提交结果", "结束本轮。"),
        ]

    def next_action(self, history):
        index = len(history)
        if index >= len(self.actions):
            return None
        tool, args, phase, intent = self.actions[index]
        return Action(tool, args, phase, intent)
