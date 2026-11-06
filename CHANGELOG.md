# 项目更新记录

每次更新单独一节，最新的放在最上面。新增记录署名统一填 `hxy`。每个文件单独一行，用仓库相对路径，写清是新增、修改还是删除，以及它在这次更新中的作用。提交或合并后补上链接。实验过程见[进度记录](docs/progress.md)。

## 2026-11-06 加入 Sphinx 7462 仓库工具和独立评测

`hxy` · 已提交 · [分支提交记录](https://github.com/wsnjxfy/repo-maintenance-agent/commits/codex/sphinx-7462-tools/) · [工具说明](experiments/sphinx_7462/README.md)

验证通过 39 项工具测试和 36 项教学 MVP 测试。原始代码通过 0/2 项修复检查，参考补丁通过 2/2 项，两者均通过 49/49 项回归检查。

| 变更 | 文件 | 这次改动的作用 |
|---|---|---|
| 新增 | `experiments/__init__.py` | 将实验代码声明为独立的 Python 包。 |
| 新增 | `experiments/sphinx_7462/__init__.py` | 声明 Sphinx 7462 实验子包，供工具与测试导入。 |
| 新增 | `experiments/sphinx_7462/prepare.py` | 校验固定源码归档，创建干净工作区并写入任务输入。 |
| 新增 | `experiments/sphinx_7462/tools.py` | 提供列文件、搜索、读取、打补丁和运行可见测试的操作，记录轨迹并导出补丁。 |
| 新增 | `experiments/sphinx_7462/evaluate.py` | 在独立副本应用候选补丁和验收测试，输出评测结果。 |
| 新增 | `experiments/sphinx_7462/grading.py` | 对照 pytest 日志与 JUnit，计算修复检查和回归检查的结果。 |
| 新增 | `experiments/sphinx_7462/requirements-windows.txt` | 固定 Windows 真题校准使用的依赖版本。 |
| 新增 | `experiments/sphinx_7462/tests/test_prepare.py` | 检查源码校验、工作区准备和输入字段隔离。 |
| 新增 | `experiments/sphinx_7462/tests/test_tools.py` | 检查工具操作、路径限制、动作预算和补丁导出。 |
| 新增 | `experiments/sphinx_7462/tests/test_evaluate.py` | 检查独立评测流程与结果判定。 |
| 新增 | `experiments/sphinx_7462/README.md` | 说明工具用途、测试命令和真题校准步骤。 |
| 新增 | `CHANGELOG.md` | 集中记录每次更新和逐文件变更。 |
| 修改 | `README.md` | 加入 Sphinx 工具说明和更新记录的入口。 |
| 修改 | `.github/workflows/check.yml` | 在 Windows 和 Ubuntu 的 CI 中增加 Sphinx 工具测试。 |

## 2026-11-03 C1 教学演示接入真实模型

Charles · 已合并 · [cbab227](https://github.com/wsnjxfy/repo-maintenance-agent/commit/cbab227ddfd70c98ef30553c36bbe3ad58930e88)

真实模型在同一教学任务上连续成功三次。文件清单以合并前的主分支为基准。

| 变更 | 文件 | 这次改动的作用 |
|---|---|---|
| 新增 | `.env.example` | 提供模型端点、模型名、API 密钥和计费参数的配置样例。 |
| 新增 | `mvp/hj10/llm.py` | 接入 OpenAI 兼容端点，构建 prompt v2，解析模型动作并记录 API 调用。 |
| 修改 | `mvp/hj10/agent.py` | 将模型用量写入轨迹，允许策略根据工具报错修正，连续报错达到上限后停止。 |
| 修改 | `mvp/hj10/tools.py` | 集中维护工具定义，统一工具派发、提示词工具表和报告标签的来源。 |
| 修改 | `mvp/hj10/report.py` | 将工具目录中的显示标签写入报告数据。 |
| 修改 | `mvp/hj10/report_template.html` | 读取报告数据里的工具标签，兼容旧报告。 |
| 修改 | `mvp/run_demo.py` | 加入真实模型入口，汇总调用数、token、费用与调用日志。 |
| 修改 | `mvp/tests/test_mvp.py` | 补充模型输出解析、工具参数、失败恢复和接口一致性测试。 |
| 修改 | `.gitignore` | 排除本地编辑器配置目录。 |
| 修改 | `HJ10_plan_and_tasks.md` | 更新 C0 环境状态、C1 验收结果和后续任务。 |
| 修改 | `README.md` | 加入真实模型配置、运行命令与结果说明。 |
| 修改 | `docs/interfaces.md` | 记录提示词的版本规则、每步一个动作的约定和多余动作的轨迹字段。 |
| 修改 | `docs/progress.md` | 记录三次真实模型运行的结果、调用开销和待解决问题。 |

## 2026-11-02 新增项目计划和接口约定

Charles · 已提交 · [5e40f22](https://github.com/wsnjxfy/repo-maintenance-agent/commit/5e40f22817fef06e0c92878865527190e2066fd8)

| 变更 | 文件 | 这次改动的作用 |
|---|---|---|
| 新增 | `HJ10_plan_and_tasks.md` | 拆分 C0–C4 的任务和验收条件，整理后续实验计划。 |
| 新增 | `docs/interfaces.md` | 约定 Agent 动作、工具目录、轨迹和运行摘要的数据格式。 |
| 新增 | `docs/progress.md` | 记录首次真实模型运行、当前进度和后续风险。 |

## 2026-10-11 首版 HJ10 教学 MVP

wsnjxfy · 已提交 · [2e17fb4](https://github.com/wsnjxfy/repo-maintenance-agent/commit/2e17fb4301ce5ebb48f086a3733c1291b67d7d6f)

用预设策略演示从阅读仓库、修改多个文件到测试失败后重试的流程。

| 变更 | 文件 | 这次改动的作用 |
|---|---|---|
| 新增 | `mvp/run_demo.py` | 创建教学仓库副本，运行 Agent、独立验收并生成报告。 |
| 新增 | `mvp/hj10/__init__.py` | 声明教学 MVP 的 Python 包。 |
| 新增 | `mvp/hj10/agent.py` | 定义动作和策略接口，控制工具执行、步数和停止条件。 |
| 新增 | `mvp/hj10/policy.py` | 提供演示用的预设策略，按测试结果展示失败恢复。 |
| 新增 | `mvp/hj10/tools.py` | 实现仓库结构分析、符号查找、文本修改和可见测试操作。 |
| 新增 | `mvp/hj10/report.py` | 将运行摘要与动作轨迹写入离线报告。 |
| 新增 | `mvp/hj10/report_template.html` | 展示每一步动作、测试结果和补丁。 |
| 新增 | `mvp/test_runner.py` | 执行指定的 unittest 测试并输出逐项结果。 |
| 新增 | `mvp/fixtures/timeout_demo/cli.py` | 提供教学任务的集成入口，串联配置解析和请求构造。 |
| 新增 | `mvp/fixtures/timeout_demo/client.py` | 构造带超时参数的教学请求。 |
| 新增 | `mvp/fixtures/timeout_demo/config.py` | 提供超时解析入口，初始实现固定返回默认值，作为教学修复起点。 |
| 新增 | `mvp/fixtures/timeout_demo/tests/test_visible.py` | 提供策略可运行的超时任务测试。 |
| 新增 | `mvp/evaluation/hidden_timeout_tests.py` | 在教学流程结束后单独检查任务需求和回归。 |
| 新增 | `mvp/tests/test_mvp.py` | 测试工具、控制流程和报告生成。 |
| 新增 | `run-demo.ps1` | 在 PowerShell 中定位可用 Python 并启动演示。 |
| 新增 | `.github/workflows/check.yml` | 在 Windows 和 Ubuntu 上自动执行教学测试和演示。 |
| 新增 | `.gitignore` | 排除本地运行产物和 Python 缓存。 |
| 新增 | `.gitattributes` | 统一文本换行格式，避免跨平台出现无关差异。 |
| 新增 | `README.md` | 介绍教学 MVP，并给出运行和测试命令。 |
| 新增 | `docs/mvp-decisions.md` | 记录教学范围、失败恢复设计和后续验证方向。 |
| 新增 | `HJ10-原始项目提示.md` | 保存课程的 HJ10 原始题面。 |
| 新增 | `HJ10-项目理解与技术路径.md` | 整理项目背景、基本概念和技术路径。 |
