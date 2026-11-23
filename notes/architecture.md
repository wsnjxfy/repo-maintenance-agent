# 分层与当前迁移状态

## 依赖方向（硬规则）

```text
contracts/       谁都可以 import；不 import 项目内模块
tools/           specs / toolsets / handlers；不 import runtime
runtime/         LocalEnvironment / Session / fsguard / process
agent_core/      唯一回路、LLM 适配、解析、报告；不 import runtime/evaluation/experiments
evaluation/      只有 runner 在 agent 结束后调用（尚未完全顶层化）
run.py           唯一编排入口
mvp/             ToyTask + scripted policy + fixture
experiments/     单题/任务适配（Sphinx 仍走旧基线）
```

## 两个容易混的概念

- **Toolset**：这次 run 开放哪些工具名；不决定环境，也不决定文件范围。
- **Visibility**：`readable` / `editable` / `protected`，属于 instance；
  由 `LocalEnvironment` 在原语层强制；`list_files` 必须过滤。

## observation v2

- `{"ok": bool, "tool": ..., "result"|"error", "kind": "tool|budget|timeout"}`；
- `fatal=True` 表示环境/加载/超时，不是工具级错误；
- `finish` 返回 `{"finished": true, "accepted": bool|null, "submission": {...}}`；
- 回路不再按 `run_tests` 名称特判。

## 当前状态（2026-11-24）

- toy 已切到 `run.py + ToyTask + LocalEnvironment + Session + handlers`；
- `mvp/demo.py` 支持 `--policy scripted|llm`，prompt v5，toy-v2 用
  `replace_text`；
- 旧 `runtime/backends/repo.py`（`ToolRunner`）与
  `experiments/sphinx_7462/baseline.py` 仍保留；
- Sphinx 7462 的四个输入不在本机，尚未做 repo 切片；
- 下一步：`SphinxTask` + `run.py`，与 `baseline.py` 对照后再删旧路径。
