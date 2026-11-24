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
- `run.py` 的 Task 接口已支持 `python` / `git`、`evaluate(run_dir, workspace, patch)`
  与 `Session.finalize()`；
- `experiments/sphinx_7462/task.py`（`SphinxTask`）已建立；真实
  `prepare`（Python 3.9.20）通过，空补丁 0/2、reference 2/2、回归 49/49；
- `experiments/sphinx_7462/runner.py` 已用 `run.py + SphinxTask` 跑通
  reference 2/2 与空补丁 0/2；
- `runtime/backends/` 与 `baseline.py` 已删除；历史版本见
  `git tag archive/sphinx-toolrunner-baseline`；
- `agent_core/llm.py` 已加入成本/ token 上限，由 `run.py` 的 summary 记录；
- 下一步：用 `--policy llm` 跑真实 Sphinx，再进入开发任务与批次实验；
  `evaluate.py` / `grading.py` 仍可后续顶层化。
