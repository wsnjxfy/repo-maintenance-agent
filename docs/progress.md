# 进度记录

> 追加式日志。计划见 [HJ10_plan_and_tasks.md](../HJ10_plan_and_tasks.md)，接口契约见 [interfaces.md](interfaces.md)。

## 2026-11-02 ｜ C1 进行中（1/3）

| 检查点 | 状态 |
| --- | --- |
| MVP 0 教学闭环（脚本策略） | 完成 |
| C0 版控与环境 | 部分完成：接口已冻结、`.env` 已建；分支、提交、独立 venv 未做 |
| C1 真实模型跑通教学任务 | **进行中：已成功 1 次，待连续 3 次** |
| C2 工具泛化 | 未开始 |
| C3 SWE-bench 单题端到端 | 未开始（Docker / WSL 未就绪） |

### 首次模型运行（证据）

- run：`mvp/runs/demo-20261102-201311-d24a4f`
- 模型：`deepseek-flash`；11 步；16 次调用；20,223 + 2,715 tokens；24.8 秒
- 结果：可见 8/8、独立需求 7/7、回归 3/3、新增回归 0、exit 0
- 行为：先读 4 个文件 → 改 config → `plan`（事后才调用）→ 改 client → 测试一次通过 → 复读 cli → finish
- 与脚本版差异：没有出现 `1/8 → 5/8` 中间态（那是脚本刻意写坏才有的）

### 本轮新增的基础设施

- 通用 OpenAI 兼容配置：`LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` / `LLM_JSON_MODE` / `LLM_PRICE_*`（`DEEPSEEK_*` 仍兼容）
- `--use-llm`：直接用 `.env` 里的模型，不必重复写模型名
- 每次调用的完整日志 `llm_calls.jsonl`（messages / raw_content / error / elapsed_ms）
- `llm.attempts` / `llm.last_error`：可定位重试原因（上次 16 次调用对 11 步 = 5 次重试）
- JSON 围栏与散文包裹的容错解析；`json_mode=auto` 遇 400 自动关闭

### 未完成 / 风险

- 连续 3 次成功：缺 2 次，且最近两次改动（日志、通用配置）尚未在真实模型上验证
- 429 / 5xx 没有退避重试 → 批量实验（C4）前必须补
- 只有步数上限，没有单次运行的 token 预算上限
- 20k prompt tokens 对 62 行仓库偏高，主要来自重复发送 history

### 下一步

1. `python -B mvp/run_demo.py --use-llm` 连跑 3 次，记录成功率、重试率、token
2. 用 `llm_calls.jsonl` 归因重试原因，决定是否改 prompt 或协议
3. 通过后进入 C2（工具泛化）

## 2026-11-03 ｜ C1 完成（3/3）

| 检查点 | 状态 |
| --- | --- |
| C0 版控与环境 | 完成：分支、提交、接口契约、`.env`、`.venv`（uv 0.9.9 / Python 3.11.15） |
| C1 真实模型跑通教学任务 | **完成：连续 3 次成功（prompt v2）** |
| C2 工具泛化 | 未开始（下一步） |
| C3 SWE-bench 单题端到端 | 未开始（Docker / WSL 未就绪） |

### 三次运行（按时间顺序）

| run | 步数 | 调用 | tokens（prompt + completion） | 耗时 | 结果 |
| --- | --- | --- | --- | --- | --- |
| `demo-20261103-140044-fcd5d0` | 11 | 11 | 14,502 + 1,389 | 17.2s | 7/7 + 3/3 |
| `demo-20261103-140326-33aa60` | 10 | 11 | 14,284 + 1,199 | 13.7s | 7/7 + 3/3 |
| `demo-20261103-140345-d45d7a` | 10 | 10 | 13,169 + 1,036 | 12.9s | 7/7 + 3/3 |

合计 45,579 tokens；三次均 `completed`、`prompt v2`、新增回归 0、exit 0。

### 观察

- **重试率从 45% 降到约 3%**（32 次调用对 31 步，仅 1 次重试）：一次一动作的 prompt、多动作容错、args 正规化共同作用。
- 步数从 11–13 降到 10–11，补丁仍然正确（7/7 + 3/3）。
- `plan` 参数层级错误未再出现（正反例 + 归一化）。
- 未观察到空 `raw_content`（`max_tokens=4096` 后未再截断）。

### 仍未解决 / 下一步

- 429/5xx 退避重试、token 预算上限（C4 前必须补）
- 每轮约 14k prompt tokens 对 62 行仓库仍偏高，来源是每步重发 history
- 进入 C2：工具泛化（去文件名白名单、`bash`、行区间读取）

## 2026-11-16 ｜ 结构调整：核心 / 工具层 / 演示分离

动机：`mvp/hj10` 同时住着「研究核心」与「教学示范」，`tools.py` 同时住着「工具规格」「玩具 backend」「测试胶水」，导致接 C3 时依赖方向会变成 agent → 单一实验。

### 目录变化（纯搬移，无行为改动）

| 变更 | 从 | 到 |
| --- | --- | --- |
| 核心套件 | `mvp/hj10/` | `agent_core/` |
| 工具规格 | `mvp/hj10/tools.py` 的 `TOOL_SPECS` 等 | `tools/specs.py` |
| 玩具 backend | `mvp/hj10/tools.py` 的 `Tools` | `runtime/backends/toy.py` |
| 真实 repo backend | `experiments/sphinx_7462/tools.py` | `runtime/backends/repo.py`（blob hash 相同） |
| 测试执行器 | `mvp/test_runner.py` + `run_suite` | `runtime/exec/unittest_runner.py` + `runtime/exec/suite.py` |
| 工具测试 | `experiments/.../tests/test_tools.py` | `runtime/tests/test_repo_backend.py` |

### 行为变化（唯一一項）

`Tools(workspace, finish_requires_tests=...)`：教学 demo 保持「测试通过才接受完成」；模型运行改为只交付结果，判定交给独立评测（`mvp/run_demo.py` 依 `model_enabled` 传入）。

### 验证

- `mvp/tests`：36 OK
- `experiments/sphinx_7462/tests`：21 OK
- `runtime/tests`：16/18，2 个失败与本机 `git 2.37.0` 行为有关（搬移前后 blob hash 相同，非重构造成）

> 同日后续（选项 A）：把原本一个 `tooling/` 再拆成 `tools/`（只有规格）与 `runtime/`（backends + exec），理由是一个叫 tooling 的文件夹不应同时装 tools 与 backend。所有 import 已更新，测试结果不变。
>
> 再后续（命名）：核心套件 `hj10/` 改名为 `agent_core/`，因为 `hj10` 是项目编号而不是角色；只改 7 行 import 与文件路径，`HJ10` 显示字串与报告标记 `__HJ10_REPORT_DATA__` 保持不变。

## 2026-11-17 ｜ toolset 层

- 新增 `tools/toolsets.py`：`toy-v1`（7 个工具）与 `repo-v1`（6 个动作），每个 toolset 绑定一个 backend。
- prompt 工具表、报告标签、dispatch 白名单全部由选中的 toolset 生成。
- 删除 `runtime/backends/repo.py` 手写的允许集合，改用 `tool_names("repo-v1")`；toy backend 同理。
- `summary` 增加 `toolset` 与 `tools_enabled`；`versions.prompt` 升到 v3（工具表由 toolset 决定）。
- 新增 3 条守门测试：toolset 内工具必须存在于 `TOOL_SPECS`、未知 toolset 报错、summary 记录 toolset。
- `--toolset repo-v1` 目前在 `run_demo` 会明确报错（adapter 尚未接）；`toy-v1` 行为不变。

## 2026-11-22 ｜ v2 落地第 1–2 步：contracts + runtime 辅助

- 新增 `contracts/`（中立层，不 import 项目内模块）：
  - `protocols.py`：`Environment` / `Policy` / `ToolSpec` 协议；
  - `protected.py`：受保护路径的唯一规则（`protected_source` 与 `is_protected`）；
  - `patch.py`：Git 补丁路径解析（`strict` 模式供评测端使用）。
- 新增 `runtime/fsguard.py`（路径安全、manifest、diff、显示截断）与 `runtime/process.py`（子进程、超时、进程树终止）。
- `runtime/backends/repo.py` 617 → 464 行；改用 `contracts` 与 `runtime` 辅助，**不再 import `experiments`**。
- `prepare.py`、`evaluate.py` 改用 `contracts.protected` / `contracts.patch`；`evaluate.py` 不再依赖 `repo.py` 的私有函数（`_write_json` 除外）。
- 验证：`mvp` 40 OK、`experiments` 53 OK；`runtime` 23 个中 2 个仍是本机 git 2.37 的既有失败（合併前即存在）。

## 2026-11-22 ｜ v2 落地第 3 步：垂直切片（合成 repo）

- 新增 `runtime/local.py`：`LocalEnvironment`（路径安全、读写、列档、manifest、子进程、`export_patch`）。
- 新增 `runtime/session.py`：phase、动作预算、墙钟、`executes_code` 审计、`finish` 原子汇出。
- 新增 `tools/handlers.py`：7 个 handler（list/read/search/patch/replace/tests/finish），只经 `env` 操作；`dispatch` 回 v2 信封（`ok`/`kind`/`fatal`）。
- `tools/specs.py` 加 `phase`、`executes_code`、`handler`；`agent_core/llm.py` 仍用 `PHASE_BY_TOOL` 填 `Action.phase`，report 尚未切换。
- `contracts/text_edit.py`：从 `runtime/` 移入，`tools/` 不再 import `runtime`。
- `agent_core/agent.py`：支援 v2 observation（`ok`/`kind`/`fatal`/`result.accepted`），toy 旧格式仍相容。
- 新增 `runtime/tests/test_vertical_slice.py`（5 个测试）：合成 repo 的 read→patch→test→finish、预算拒绝、受保护档拒绝、失败补丁原子性、Agent 全链。
- 验证：`mvp` 40 OK、`runtime` 28（2 个仍是本机 git 2.37）、`experiments` 53 OK、demo 7/7。

## 2026-11-22 ｜ 落地：instance visibility

- 新增 `contracts/visibility.py`：`Visibility(readable, editable, protected)`，POSIX glob（`**` 跨目录、`*` 单段、无斜线按 basename）；`protected` 同时套用 `contracts.protected` 的通用规则。
- `LocalEnvironment(visibility=...)`：
  - `read_text`/`read_bytes` 只允许 readable；`list_files` 过滤 readable；
  - `write_text`/`write_bytes`/`delete` 只允许 editable 且非 protected；
  - `exists`/`is_file` 对不可读路径回 `False`；`manifest()` 只覆盖 readable。
- `tools/handlers.py` 改用 `env.require_editable`，不再自己判断受保护路径。
- `experiments/sphinx_7462/prepare.py` 的 `config.json` 增加 `visibility`（repo 预设 `**` + protected 清单）。
- 新增 `runtime/tests/test_visibility.py`（6 个测试）：glob 语义、读写边界、protected、`list_files`/`manifest` 过滤。
- 验证：`mvp` 40 OK、`runtime` 34（2 个仍是本机 git 2.37）、`experiments` 53 OK、demo 7/7。
- 待办：toy demo 尚未改用 visibility；`tools/toolsets.py` 的 `"backend"` 字段尚未移除；production（`run_demo`/`baseline`）尚未切到 `Session + LocalEnvironment`。

## 2026-11-22 ｜ M1 第 1–2 步：v2 toy demo

- 新增 `mvp/toy_config.py`：`toy-v2` toolset、`Visibility`（readable 4 档、editable 3 档）、`PROTOCOL`。
- 新增 `mvp/scripted_policy.py`：用 `list_files`/`read_lines`/`apply_patch`/`run_visible_tests`/`finish` 的教学脚本（不再用 toy 专属工具）。
- 新增 `mvp/demo.py`：复制 fixture → `git init` + baseline commit → `LocalEnvironment` + `Session` + `Agent` + `evaluate()`，产出 `summary.json`/`final.patch`/`report.html`。
- `tools/toolsets.py` 新增 `toy-v2`（5 个工具，backend=`local`）。
- 新增 `mvp/tests/test_demo.py`（4 个测试）：demo 完成且 7/7+3/3、补丁包含修复、trajectory 为 v2 observation、visibility 保护可见测试。
- CI：`pip install pytest` 移到 `mvp/tests` 之前（demo 需要 pytest）。
- 验证：`mvp` 44 OK、`runtime` 34（2 个仍是本机 git 2.37）、`experiments` 53 OK、`python mvp/demo.py` exit 0（7/7、3/3）。
- 待办：`run_demo.py` 改薄壳、删 `runtime/backends/toy.py`、拆 `test_mvp.py`、移除 toolset `backend` 字段、prompt v4。

## 2026-11-22 ｜ M1 第 3–5 步：切換與清理

- 抽出 `mvp/evaluation/demo_eval.py`（`FIXTURE`/`TASK`/`evaluate`/`final_diff`），切斷 `demo` ↔ `run_demo` 的循環依賴。
- `mvp/run_demo.py` 改為薄殼，實際流程在 `mvp/demo.py`；CLI 只保留 `--max-steps`/`--output-dir`/`--open`。
- 刪除 `runtime/backends/toy.py` 與 `agent_core/policy.py`（`ScriptedPolicy` 由 `mvp/scripted_policy.py` 取代）。
- `tools/toolsets.py`：只留 `toy-v2` / `repo-v1`，移除 `"backend"` 欄位；`DEFAULT_TOOLSET = toy-v2`。
- 測試遷移（`mvp/tests/test_mvp.py` 已刪除）：
  - `agent_core/tests`：22 個（LLM 16、report 1、agent 5）；
  - `tools/tests`：4 個（catalog/toolset）；
  - `runtime/tests`：39 個（含 `test_exec.py` 5 個、`test_visibility.py` 6 個、slice 5 個、repo 23 個）；
  - `mvp/tests`：8 個（demo e2e、artifacts、hidden 隔離、budget、regression）。
- `PROMPT_VERSION` 升 **v4**；SYSTEM_TEMPLATE 的範例改引用 `apply_patch`/`list_files`/`run_visible_tests`。
- CI 增加 `agent_core/tests` 與 `tools/tests` 兩步。
- 驗證：`agent_core` 22 OK、`tools` 4 OK、`runtime` 39（2 個仍是本機 git 2.37）、`mvp` 8 OK、`experiments` 53 OK；`mvp/demo.py` 與 `mvp/run_demo.py` 都 exit 0（7/7、3/3）。
- 待辦：Sphinx 切片（`run.py` + `Session`）、刪舊 `ToolRunner`、`evaluation/` 頂層化、把模型接入新的 demo/runner。

## 2026-11-22 ｜ CI 修正：Windows cp1252 輸出

- GitHub Actions 的 Windows job 以 cp1252 作為 stdout 編碼；`Agent` 的中文進度行拋 `UnicodeEncodeError`，讓 `test_demo` 的 `setUpClass` 失敗（0 tests）。
- `agent_core/agent.py` 加 `_emit()`：`print` 失敗時以 `errors="replace"` 重印，不再中斷回路。
- `mvp/demo.py` 的 `run()` 開頭把 stdout 重設為 UTF-8（`errors="replace"`），涵蓋 demo 自己的中文輸出。
- 以 `PYTHONIOENCODING=cp1252` 模擬：`mvp` 8 OK、`agent_core` 22 OK、`tools` 4 OK、`runtime` 39（2 個本機 git 2.37）、`experiments` 53 OK、`run_demo` exit 0。

## 2026-11-23 ｜ M2 第 1 步：抽出通用 `run.py`

- 新增根目錄 `run.py`：`Task` 協議 + `run(task, policy, output_root, max_steps)`；負責建 run dir、`task.prepare()`、`LocalEnvironment`、`Session`、`Agent`、`task.evaluate()`、summary/report。
- 新增 `mvp/toy_task.py`：`ToyTask`（fixture、`Visibility`、`PROTOCOL`、`prepare()` 的 git baseline commit、`evaluate()`）；吸收原 `mvp/toy_config.py` 後刪除該檔。
- `mvp/demo.py` 縮成薄 CLI（`ToyTask` + `ToyV2Policy` → `run.py`）；`mvp/run_demo.py` 只委派 `mvp.demo.main`。
- 新增 `mvp/__init__.py`；測試改 import `mvp.demo` / `mvp.toy_task`。
- 驗證：`mvp` 8 OK、`agent_core` 22 OK、`tools` 4 OK、`runtime` 39（2 個本機 git 2.37）、`experiments` 53 OK；`mvp/demo.py` 與 `mvp/run_demo.py` 都 exit 0（7/7、3/3）。
- 待辦：M2 第 2 步把 `LLMPolicy` 接進 `run.py`（`--policy llm`），第 3 步吸收預算，之後 Sphinx Task。
