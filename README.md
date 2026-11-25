# Repository Maintenance Agent · HJ10

一个从教学原型逐步发展的仓库维护 Agent 项目。目前完成 **MVP 0：看懂执行闭环** 与 **C1：接入真实模型并连续 3 次跑通教学任务（prompt v2）**；toy 演示已切到 v2 共用栈（`LocalEnvironment + Session + tools/handlers`），下一步是 C2/Sphinx 切片。

这版默认用预设脚本提出操作；加 `--policy llm` 可接入真实模型。toy 工具集是 `list_files`、`read_lines`、`replace_text`、`run_visible_tests`、`finish`（片段替换为主）；repo/Sphinx 仍用 `apply_patch`。运行后生成可逐步浏览的中文离线报告。C1 的连续三次成功只说明旧 scaffold 可用；**单次或少量成功不构成模型能力或方法优劣的结论**。

Sphinx 7462 [工具说明](experiments/sphinx_7462/README.md) · [团队更新记录](CHANGELOG.md) · [开发笔记](notes/README.md)

## 立即运行

需要 Python 3.10+；toy demo 会用 pytest 运行可见测试（CI 会自动安装 `pytest==8.3.3`）。

```sh
python mvp/run_demo.py --open
```

接入真实模型（任何 OpenAI 兼容端点）。先在 `.env` 填好 `LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`（模板见 `.env.example`）：

```sh
python mvp/demo.py --policy llm --model deepseek-flash --open
```

不指定 `--model` 时使用 `.env` 的 `LLM_MODEL`。模型调用会写 `llm_calls.jsonl`（`--no-llm-log` 可关闭）。

Windows PowerShell 可使用包装脚本，它会寻找已有 Python（包括本机 Codex 提供的运行时）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run-demo.ps1 -OpenReport
```

每次运行建立新的 `mvp/runs/toyv2-时间-随机后缀/`，不修改原始教学仓库。打开其中的 `report.html`，点击步骤或使用上一／下一步查看过程。浏览器报告完全离线，按钮用于查看已执行的记录，不会再次修改文件。

### 命令行参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--policy` | `scripted` | `scripted`（教学脚本）或 `llm`（真实模型） |
| `--model NAME` | `.env` 的 `LLM_MODEL` | 仅 `--policy llm` 使用 |
| `--max-steps N` | 16 | 决策步数上限；耗尽 → `budget_exhausted`，退出码 1 |
| `--output-dir PATH` | `mvp/runs` | run 目录的父层（仍会建唯一子目录） |
| `--open` | 关 | 跑完用浏览器打开 `report.html` |
| `--no-llm-log` | 关 | 不写 `llm_calls.jsonl`（仅模型模式） |

默认脚本策略不调用模型；退出码 0 表示 `completed` 且独立评测通过。完整说明以 `python -B mvp/run_demo.py --help` 为准。

## 通用启动 CLI 与 Web 门户

比较不同工具组合 / 策略 / 模型时，用注册表驱动的通用入口，而不是各样例各写一套 CLI。样例定义在 `tasks/registry.py`，新增样例只需加一条 `TaskEntry`。

```sh
# 玩具样例，脚本策略（免 API key）
python -m scripts.run_task --task toy --policy scripted

# 只开部分工具：工具子集会同时作用于执行层白名单与 LLM 提示
python -m scripts.run_task --task toy --policy scripted \
    --tools list_files,read_lines,replace_text,run_visible_tests,finish

# Sphinx（需要 data/ 与 py3.9 venv）：先用 reference 免费冒烟
python -m scripts.run_task --task sphinx-7462 --policy reference
```

`summary.json` 新增 `tools_enabled`，记录这次实际启用的工具清单；`tool_versions` 记录每个启用工具的版本快照（初始皆为 `v0`）；`retry_policy` 记录解析/DSML 重试上限。

**本机 Web 门户**（在浏览器里配置、一键开始、实时看每一步、跑完直接开 `report.html`）：

```sh
pip install -r requirements-web.txt   # 仅门户需要 fastapi/uvicorn；核心代码仍零依赖
python -m web.server                  # 默认 http://127.0.0.1:8000
```

左侧选样例 / 策略 / 模型并勾选工具（`finish` 固定、至少一个编辑工具），右侧实时显示步骤、日志、花费与结果链接，下方是历史 run 对照表。历史 run 会写入 `runs/web/<id>/web-run.json`（另有 `web.log`），重启门户后仍会加载；表格显示每次使用的 prompt 版本与每个 tool 的版本，点击历史行可回看该次日志、步骤与报告。按 **开始** 前可跑 preflight 检查数据档、解释器与 API key。

安全约束：只绑 `127.0.0.1`（会执行任意仓库代码）；API key 留在服务端由子程序读取，不下发浏览器；artifacts 只在该 run 目录内取档（拒绝 `..`）；默认同时只允许 1 个 run。

## 演示内容

任务：让一个小型 Python 下载请求支持超时配置。CLI 优先于配置，未提供时保持 5；最终选中值须为正的有限整数或浮点数（拒绝 bool 和字符串）；仅校验选中值，保留 URL 和默认请求行为。这里构造请求字典，不发起真实网络请求，也不演示重试实现。

```text
list_files → read_lines（config/client/cli/可见测试）→ 起点测试
  → replace_text（config.py）→ replace_text（client.py）→ 重测 → finish
  → 独立需求与回归评测
```

在当前固定教学任务上，可见测试从 **1/8**（只有默认值用例通过）变为 **8/8**；最终独立需求测试 **7/7**、回归 **3/3**。这是专门设计来解释失败恢复的样例，不是对陌生仓库的泛化成绩。

可以降低预算，观察未完成状态：

```sh
python mvp/run_demo.py --max-steps 3
```

`--max-steps` 是 **demo 的决策步数上限**，不是测试指令；步数耗尽会返回 `budget_exhausted` 与非零退出码，并保存轨迹和独立评测，不伪装成成功。跑测试请用下面的 `unittest` 指令。

测试结果按方法计数；跳过和预期失败不计为验收通过。测试加载失败、执行超时与环境故障分别记录，加载失败或超时的具体原因需要查看日志，可能来自代码，也可能来自环境。

## 从哪里读代码

| 文件 | 关注的问题 |
| --- | --- |
| `run.py` | 通用 orchestrator：Task → Environment → Session → Agent → Evaluator |
| `mvp/demo.py` | 薄 CLI：`ToyTask` + 脚本策略，呼叫 `run.py` |
| `mvp/toy_task.py` | toy 的 fixture、visibility、protocol、prepare/evaluate |
| `mvp/scripted_policy.py` | 教学脚本决策 |
| `mvp/run_demo.py` | 兼容 CLI 薄壳（`python mvp/run_demo.py`） |
| `agent_core/agent.py` | 模型/策略怎样与工具交替执行，何时停止 |
| `agent_core/llm.py` | OpenAI 兼容适配器：prompt、解析、用量与调用日志 |
| `tools/specs.py` | 工具目录：名称、参数、说明（prompt 与报告标签的唯一来源） |
| `tools/toolsets.py` | 工具集：每次 run 可用哪些工具（不绑定 backend） |
| `tools/handlers.py` | 工具语义：参数校验、受保护路径、结果整理；操作委派给 env |
| `runtime/local.py` | `LocalEnvironment`：路径安全、visibility、读写、子进程、补丁导出 |
| `runtime/session.py` | 预算、墙钟、完整性审计、`finish` 冻结 |
| `runtime/exec/` | 新进程运行 unittest，返回结构化测试结果 |
| `agent_core/report.py` / `report_template.html` | 将实际轨迹呈现为离线报告 |
| `mvp/fixtures/timeout_demo/` | 保留初始缺陷的三文件教学仓库及公开测试 |
| `mvp/evaluation/hidden_timeout_tests.py` | 只供独立评测器使用的示范验收检查 |
| `experiments/sphinx_7462/task.py` | `SphinxTask`：真实实例的 prepare/evaluate 适配 |
| `experiments/sphinx_7462/runner.py` | 用 `run.py + SphinxTask` 跑 reference / none / llm |

建议先读 `agent_core/agent.py`，再对应报告看每一次 `Action → observation`，最后看 `tools/handlers.py` 与 `runtime/local.py`。源码注释中的 `Policy.next_action(history)` 是接入模型的接口位置。

## 运行产物

- `workspace/`：本轮修改后的仓库副本（含 baseline commit）。
- `task.txt`：本轮任务说明。
- `trajectory.jsonl`：每一步的意图、工具参数、实际反馈与耗时。
- `final.patch`：初始代码到最终代码的统一 diff（由 `Session.finish` 导出）。
- `summary.json`：停止状态、步数/动作数、模型、token/费用、toolset、env、visibility、评测和耗时。
- `llm_calls.jsonl`：仅 `--policy llm`；每次 API 调用的完整请求与原始响应。
- `controller/`：`state.json`、`actions.jsonl`、`submission/` 等运行记录。
- `report.html`：可直接打开的中文轨迹浏览器。

运行目录默认不提交到 Git。原始 fixture 里的测试会失败，这是任务的起点；维护系统自身的检查请运行：

```sh
python -B -m unittest discover -s agent_core/tests -v
python -B -m unittest discover -s tools/tests -v
python -B -m unittest discover -s runtime/tests -v
python -B -m unittest discover -s mvp/tests -v
python -B -m unittest discover -s experiments/sphinx_7462/tests -v
```

## 已有边界与下一步

当前支持固定的 toy 教学仓库与 Sphinx 7462 单题。模型可读/可写哪些文件由 Task 的 `Visibility` 决定（tests 只读）；测试在独立 Python 进程中执行。这些边界**不构成操作系统级沙箱**，不要将本版直接用于运行任意外部仓库。

评测源码不进入任务工作区，也不通过工具或反馈交给策略；它随仓库公开，目的是展示独立验证的组织方式，不能作为正式研究中的保密测试集。教学策略事先知道样例修法，也不能用于评估检索质量。

后续按小步叠加：

1. 用 `run.py + SphinxTask + LLMPolicy` 在真实 Sphinx 上跑模型；预算由 `llm.py` 的成本/token 上限控制。
2. 在少量开发任务上比较普通 Agent 与人工提供相关修改前代码，判断模型/上下文瓶颈。
3. 根据结果只选择一个核心机制改进，先考虑上下文选择；固定模型和预算做对照与消融。
4. 建立独立开发/测试任务、隔离执行及多次实验，再讨论成功率与回归收益。

具体模型、语言范围和任务来源尚未确定。课程评分细则、投入、完整硬件与预算仍待确认。

## 课程依据与学习记录

- [原始项目提示](HJ10-原始项目提示.md)
- [项目理解与技术路径](HJ10-项目理解与技术路径.md)
- [MVP 决策记录](docs/mvp-decisions.md)

参考原始资料：[mini-SWE-agent](https://mini-swe-agent.com/latest/)、[SWE-agent 论文](https://arxiv.org/abs/2405.15793)、[SWE-bench](https://www.swebench.com/SWE-bench/)。本版未引入这些项目的运行依赖。
