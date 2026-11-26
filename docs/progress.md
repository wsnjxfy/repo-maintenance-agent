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

## 2026-11-23 ｜ M2 第 2 步：LLM policy 接回 `run.py`

- `run.py` 增加 `policy_factory(run_dir, task)`：讓 LLM policy 能把 `llm_calls.jsonl` 寫進 run 目錄；`policy` 與 factory 二選一。
- `mvp/demo.py` 增加 `--policy {scripted,llm}`、`--model`、`--no-llm-log`；LLM 分支用 `policy_from_env(task.problem, model, toolset=task.toolset, transport=..., log_path=...)`。
- 新增 `mvp/tests/test_demo_llm.py`（4 個測試，`FakeTransport` 不連網；數字已按後續 prompt v5 更新）：
  - 10 次假回應 → `completed`、7/7、3/3、`prompt=v5`、tokens 100/50、`llm_calls.jsonl` 10 筆；
  - scripted 仍是 `prompt=v1`、無 token/log；
  - `--no-llm-log` 不產生檔案；
  - prompt 只列 `toy-v2` 的 5 個工具。
- README 補回 `--policy llm` 用法與 CLI 參數。
- 驗證：`mvp` 12 OK、`agent_core` 22 OK、`tools` 4 OK、`runtime` 39（2 個本機 git 2.37）、`experiments` 53 OK；`python mvp/demo.py` exit 0（7/7、3/3）。
- 待辦：真實模型手動跑一次、第 3 步預算吸收、Sphinx Task。

## 2026-11-23 ｜ toy-v2 改用 `replace_text`（prompt v5）

- 背景：`deepseek-flash` 在新 `apply_patch` 上連續產生 hunk 行數錯誤的 diff（`corrupt patch`），而 C1 用舊 `replace_text` 成功；為把「工具格式」與「模型能力」分開，toy 改回片段替換。
- `tools/toolsets.py`：`toy-v2` 的 `apply_patch` → `replace_text`；`repo-v1` 不動。
- `agent_core/llm.py`：`PROMPT_VERSION = v5`；`system_prompt()` 依 toolset 選 `replace_text` 或 `apply_patch` 範例，避免 repo 看到不存在的工具。
- `mvp/scripted_policy.py`：改為兩次 `replace_text`（config.py、client.py），刪除 `PATCH`。
- `tools/handlers.py`：`dispatch` 補捕捉 `SyntaxError`；新增 `tools/tests/test_handlers.py`（唯一片段、缺片段、語法錯誤不寫入）。
- 測試更新：`toy-v2` 5 個工具、scripted 步驟 9→10、fake LLM 10 calls / 100+50 tokens、prompt 只含 `replace_text` 不含 `apply_patch`。
- 驗證：`agent_core` 23 OK、`tools` 7 OK、`runtime` 39（2 個本機 git 2.37）、`mvp` 12 OK、`experiments` 53 OK；`python mvp/demo.py` 10 步、7/7、3/3。
- 待辦：用真實 `deepseek-flash` 重跑 toy；若成功，再決定 repo 是否也做 recount 或保留 `apply_patch`。

## 2026-11-24 ｜ CI 修正：run.py 中文輸出（cp1252）

- GitHub Actions run 24（`5906d81`）與 run 25（`cdafbbc`）：Ubuntu 綠，Windows 的 `mvp/tests` 在 `run.py:115` 的 `print` 失敗：
  `UnicodeEncodeError: 'charmap' codec can't encode ...`。
- 原因：`Agent` 已有 `_emit` 容錯，但新加的通用 `run.py` 直接 `print`；`test_demo` 直接呼叫 `demo.run()`，不經過 `main()` 的 stdout reconfig。
- `run.py`：新增 `_configure_stdout()`（UTF-8、`errors="replace"`）與 `_emit()`，套用到五個進度輸出。
- 防禦性保留 `tools/handlers.py` 的 CRLF 正規化與 `test_crlf_checkout_accepts_lf_fragments`（Windows checkout 可能 CRLF；不是本次失敗主因）。
- 以 `PYTHONIOENCODING=cp1252` 模擬：`mvp` 12 OK、`run_demo` exit 0；正常：`agent_core` 23、`tools` 8、`runtime` 39（2 個本機 git 2.37）、`experiments` 53、`mvp` 12。

## 2026-11-24 ｜ Sphinx 校準輸入就位

- 四檔從 `sphinx-7462-calibration-inputs/` 移至 `data/sphinx-doc__sphinx-7462/`；舊根目錄已移除。
- `review-manifest.json` 改名為 `review.json`，與 `experiments/sphinx_7462/README.md` 一致；CLI 參數仍是 `--review-manifest`（語義名稱，不是檔名）。
- `.gitignore` 增加 `data/` 與 `runs/`；`mvp/runs/` 保留給 toy 產物。
- `experiments/sphinx_7462/README.md` 的輸入路徑改為 `data/sphinx-doc__sphinx-7462/...`，輸出改為 `runs/...`。
- 驗證：tarball SHA 符合；`checked_members` 通過；`_validated_record` 得到 2 個 F2P + 49 個 P2P；test_patch SHA 符合 README；`reference.patch` 等於 record 的 `patch`；兩個補丁 `git apply --check` 都通過。
- 待辦：用 Python 3.9.20 + pytest 8.3.3 跑真實 `prepare` / `evaluate`（0/2、2/2、49/49）。

## 2026-11-24 ｜ M3 階段 0–2：SphinxTask 與 Task 介面

- 真實 `prepare`（Python 3.9.20，`experiments/sphinx_7462/.venv39`）成功：1398 個原始檔、735 個受保護檔、`environment-probe=passed`。
- 舊 `evaluate` 基線校準：空補丁 0/2 + 49/49（exit 1）、reference 2/2 + 49/49（exit 0）。
- `run.py` Task v2：`python` / `git`、`evaluate(run_dir, workspace, patch)`、`resolved` 正規化；Agent 未 `finish` 時 `Session.finalize()` 匯出補丁。
- `LocalEnvironment.run()` 把 `TEMP` / `TMP` 指到 `scratch/tmp`；`SphinxTask.evaluate()` 用絕對 `run_dir/tmp`，避免 pytest 因 DSH 暫存 ACL 退回 workspace。
- `prepare.py` 新增 `prepare_workspace()`；`experiments/sphinx_7462/task.py` 的 `SphinxTask` 用它，加入 baseline commit、visibility 與正規化評測。
- 新路徑驗證：`SphinxTask.prepare` 真實 workspace、空補丁 0/2 + 49/49、reference 2/2 + 49/49。
- 測試：`agent_core` 23、`tools` 8、`runtime` 41（2 個本機 git 2.37）、`mvp` 12、`experiments` 56（新增 3 個 SphinxTask 測試）。
- 待辦（第 3 階段）：用 `run.py + SphinxTask` 跑 agent（先參考補丁/腳本策略），與 `baseline.py` 對照，之後刪 `ToolRunner` / `baseline.py` 並把 `evaluation/` 頂層化。

## 2026-11-24 ｜ M3 第 3 階段：Sphinx agent 路徑與 legacy 移除

- `evaluate.py` 的 `_write_json` 移至 `runtime/fsguard.write_json`，不再依賴 `runtime.backends`。
- `agent_core/llm.py` 加入 `budget_usd` / `max_total_tokens` 與 `PolicyBudgetExceeded`；`Agent` 回報 `budget_exhausted`；新增 4 個預算測試。
- `experiments/sphinx_7462/runner.py`：`--policy reference|none|llm`，用 `run.py + SphinxTask` 執行。
- 實測：reference **2/2 + 49/49**、空補丁 **0/2 + 49/49**，exit code 0/1 符合預期。
- 刪除 `experiments/sphinx_7462/baseline.py`、`runtime/backends/`、`runtime/tests/test_repo_backend.py` 及 3 個依賴 baseline 的測試；歷史版本見 `git tag archive/sphinx-toolrunner-baseline`。
- 測試數：`agent_core` 28、`tools` 8、`runtime` 18、`mvp` 12、`experiments` 37；合計 **103**，本機全綠（舊 2 個 git 2.37 失敗隨 legacy 測試移除）。
- 待辦：`--policy llm` 的真實 Sphinx run、開發/測試任務批次、`evaluation/` 頂層化。

## 2026-11-25 ｜ prompt v6 與 summary 保證

- `agent_core/llm.py`：`PROMPT_VERSION = v6`。system prompt 改為精簡的「純 JSON 動作清單」，移除「可用工具／調用」措辭與反面範例；重試訊息只要求一行 JSON，不再把 `KeyError`／`JSONDecodeError` 字串塞回 prompt。
- `run.py`：prepare、agent、finalize、evaluate 任一階段失敗都會寫 `summary.json`；`error` 與 `error_stage` 記錄失敗階段；evaluate 失敗保留 agent 的 status，另記 `evaluation.status="evaluation_error"`；report 失敗記 `report_error` 但不影響 summary。
- `scripts/run_task.py` 路徑不再因 evaluate 例外只印 argparse 錯誤；`mvp/demo.py` 的退出碼改看 summary 的 `resolved`。
- 實測 `deepseek-flash`：prompt v6 下 toy 任務 14 步完成、7/7 + 3/3、`resolved=true`，無額外重試；Sphinx `--policy reference` 以 `run.py + SphinxTask` 跑出 2/2 + 49/49、exit 0。
- 測試：`agent_core` 31、`tools` 11、`runtime` 20、`mvp` 12、`experiments` 37、`web` 13，全綠。
- 待辦：DSML 仍可能偶發（prompt v6 的 30 次重放中 1 次 DSML，其餘 JSON 或截斷；重試訊息可回收）；若要根治，下一步改 OpenAI `tools` 協議。

## 2026-11-25 ｜ canonical evaluation 與報告修正

- 新增 `contracts/evaluation.py`：`resolved`／`status`／`execution_valid`／`metrics`／`new_regressions`／`errors`／`cases`／`details` 的共用 envelope。
- `ToyTask.evaluate()` 把 `requirements`／`regressions`／`baseline_regressions` 映射成 `fail_to_pass`／`pass_to_pass`／`baseline`；`SphinxTask.evaluate()` 把 `grading.f2p_*`／`p2p_*` 映射成同兩個指標，原始 payload 收進 `details`。
- `run.py` 的 `resolved` 只讀 canonical `resolved`，終端輸出改印 `metrics`；summary 增加 `final_diff`，報告可顯示完整補丁。
- `agent_core/report_template.html`：評測區塊改為渲染 canonical `metrics`（含 label／passed／total／failed），支援 `resolved`、`new_regressions`、`errors`、逐項 `cases` 與原始 `details`；最終補丁改讀 `summary.final_diff`。舊 toy schema 保留 fallback。
- `web/static/app.js` 的結果面板同步改讀 `metrics`。
- 驗證：toy 7/7 + 3/3；Sphinx reference 2/2 + 49/49；用 headless Chrome 渲染失敗的 Sphinx summary（F2P 0/2、P2P 49/49、2 個失敗案例）確認報告不再顯示「未記錄」。
- 測試：`agent_core` 31、`tools` 11、`runtime` 20、`mvp` 12、`experiments` 37、`web` 13，全綠。

## 2026-11-25 ｜ Web run 歷史持久化與 Sphinx 路徑修正

- `web/server.py`：每個 run 寫 `runs/web/<id>/web-run.json`（config、created、status、returncode）；啟動時 `load_runs()` 掃描 `runs/web/`，載入 `web.log` 與 trajectory；沒有 metadata 的舊 run 由 `summary.json` 推斷 task/policy/status。
- 關掉 server 後重開仍會列出歷史；點歷史列可回看 logs/steps/report；SSE 對已完成 run 會重放日誌後送 `done`。
- `tasks/registry.py`：Sphinx 預設 Python 改為依平台找 `experiments/sphinx_7462/.venv39/Scripts/python.exe`（Windows）或 `bin/python`，支援 `SPHINX_PYTHON` 覆寫；修正入口的 `FileNotFoundError`。
- `requirements-web.txt` 加入 `httpx2`（FastAPI TestClient 需要）；`README` / `notes/testing.md` 同步。
- 測試：核心 111 + `web` 16，全綠。

## 2026-11-25 ｜ 每個 tool 的版本快照

- `tools/specs.py`：12 個 tool 都加 `version: 0`（source of truth）。
- `tools/toolsets.py`：新增 `tool_versions(names)`，只快照指定工具。
- `run.py`：run 開始時把 `tool_versions` 寫入 `summary.json`；失敗的 run 也會保留已選工具的版本。
- `web/server.py`：`web-run.json` 的 config 加入同一份 snapshot，`/api/runs` 回傳 `tool_versions`。
- `web/static`：歷史表新增「tools 版本」欄，逐項顯示 `tool vN`；舊 run 沒有欄位時顯示 `—`。
- 新增 `tools/tests/test_versions.py` 與 `notes/tool-versioning.md`；版號仍需人工在 handler/spec 變更時 +1。
- 測試：`agent_core` 31、`tools` 14、`runtime` 20、`mvp` 12、`experiments` 37、`web` 16，全綠。

## 2026-11-25 ｜ Prompt v7：通用 workspace hint

- `agent_core/llm.py`：`PROMPT_VERSION = "v7"`；新增 `DEFAULT_WORKSPACE_HINT`，移除 `tests/test_visible.py` 的 task-specific 句子。
- `LLMPolicy` 接受 `workspace_hint`，Task 可 override；`scripts/run_task.py` 會傳入 `task.workspace_hint`。
- `run.py`：summary 增加 `prompt_version`（`versions.prompt` 保留）；舊 run 仍可從 `versions.prompt` 讀取。
- `web/server.py`：LLM run 的 `web-run.json` config 記錄 `prompt_version`；歷史表新增 prompt 欄，舊 run 從 summary fallback。
- 測試：新增 prompt hint 回歸測試；`agent_core` 33、`tools` 14、`runtime` 20、`mvp` 12、`experiments` 37、`web` 17，全綠。
- 注意：舊 Sphinx LLM run 使用 v6，含 `tests/test_visible.py` 的誤導，不能和新 v7 結果直接比較。

## 2026-11-25 ｜ 解析與 DSML 重試分開計數

- `LLMPolicy`：新增 `max_parse_attempts`（正常非法 JSON）與 `max_dsml_retries`（DSML/XML 工具標記）；預設 2 / 2。
- DSML 失敗不增加 `parse_attempts`，但每次 API 呼叫仍計入 `model_calls`、tokens、cost；另有 `max_api_calls = max_parse_attempts + max_dsml_retries` 防止無限重試。
- `last_usage` 增加 `parse_attempts`、`dsml_retries`；`summary` 增加 `retry_policy`。
- `policy_from_env` 讀 `LLM_MAX_PARSE_ATTEMPTS` / `LLM_MAX_DSML_RETRIES`；`scripts/run_task.py` 加 `--max-parse-attempts` / `--max-dsml-retries`。
- 測試：`agent_core` 37、`tools` 14、`runtime` 20、`mvp` 12、`experiments` 37、`web` 17，全綠。

## 2026-11-25 ｜ 修正 finalize 的 WindowsPath 序列化錯誤

- 原因：`SphinxTask.python` 是 `Path`，傳入 `LocalEnvironment` 後成為 `HandlerContext.python`；`run_visible_tests` 把它放進回傳的 `command` 清單，`Session` 將 `last_visible_tests` 寫入 `state.json` 時 `json.dumps` 失敗，最後 `finalize` 的 `submission.json` 也失敗。
- `LocalEnvironment.__init__` 現在把 `python` / `git` 正規化為字串。
- `runtime/session._write_json` 加 `default=str` 作防禦；新增 `runtime/tests/test_path_serialization.py`。
- 測試：`agent_core` 37、`tools` 14、`runtime` 21、`mvp` 12、`experiments` 37、`web` 17，全綠。

## 2026-11-26 ｜ max_actions 成為可調參數

- 原因：`max_steps=60` 只放寬 Agent 決策步數；Sphinx protocol 的 `max_actions=30` 仍提早觸發 `Workspace action limit reached`。
- `run.py`：新增 `max_actions` 參數（`DEFAULT_MAX_ACTIONS=60`），複製並覆寫 `task.protocol` 後交給 `Session`；summary 記錄實際生效的 `max_actions`。
- `scripts/run_task.py`、`experiments/sphinx_7462/runner.py`、`mvp/demo.py` 新增 `--max-actions`（預設 60）。
- Web：`RunRequest.max_actions` 預設 60；進階表單新增 `max actions` 欄位；狀態列顯示 `actions / max_actions`；`web-run.json` 與歷史 run 記錄此設定。
- 驗證：toy CLI 預設 `max_actions=60` 正常完成；`--max-actions 5` 在 actions=5、steps=6 時以 `Workspace action limit reached` 停止；全套件綠。

## 2026-11-26 ｜ context 持久摘要、loop guard 與 agent version

- 背景：Sphinx LLM run 花 20 分鐘、讀同一檔案 92 次；`LLMPolicy` 只保留最近 6 步、每步 observation 截到 1200 字元，模型早先的修改/測試/搜尋線索會消失。
- `agent_core/llm.py`：prompt v8。新增由完整 history 生成的持久摘要（已讀檔案與範圍、已修改、已跑測試、搜尋、搜尋發現但未讀的檔案），以及重複讀取/久未修改警告。
- `read_lines` observation 改成保留可顯示行並標記 `shown_until` / `omitted` / 續讀起點；測試 observation 保留 status/counts 與 stdout tail，不再被 JSON 截斷吃掉狀態。
- `max_history`、`observation_limit`、`notes_limit`、`read_repeat_warning`、`stale_edit_steps` 可由 `LLM_*` 環境變數調整；summary 記錄 `context_policy`。
- `agent_core.AGENT_VERSION = "v1"`；`summary.versions.agent`、`summary.agent_version` 記錄，Web 歷史表新增 agent 欄位，舊 run 顯示 `—`。
- 驗證：用上次失敗 run 的完整 history 重算摘要，會顯示 `sphinx/pycode/ast.py` 為「搜索發現但尚未讀取」並警告 `sphinx/domains/python.py` 已讀 91 次；toy scripted 與 LLM 測試通過；全套件綠。

## 2026-11-26 ｜ show_diff 明確標示 staging 行為

- 採用方案 B：保留 `show_diff` 的 `git add -A`（用來把新檔案納入 diff），不再假裝它是唯讀。
- `tools/specs.py`：`show_diff` 的 `doc` 明確寫出會 stage 變更；`mutating` 由 `False` 改為 `True`；版本由 0 升到 1。
- `tools/handlers.py`：handler docstring 說明 staging 副作用；`changed_files` 改用 `git diff --cached --name-only -z HEAD`，補上刪除檔與含空白路徑。
- 測試：新增 `show_diff` staging 與刪除檔回歸測試，以及 spec 描述／`mutating` 的 catalog 測試；全部套件綠。

## 2026-11-26 ｜ Web 先選 toolset，再勾工具；新編輯工具接入

- `web/server.py`：`EDIT_TOOLS` 加入 `create_file`、`edit_lines`；`RunRequest` 新增 `toolset`；`/api/catalog` 回傳 `toolsets`（名稱＋工具清單）；`_normalize_tools` 驗證工具必須屬於所選 toolset，並依 toolset 提供全量工具。
- `scripts/run_task.py`：新增 `--toolset`；未給 `--tools` 時使用該 toolset 全部工具；給 `--tools` 時驗證其為 toolset 子集；summary 記錄實際 toolset。
- `tasks/registry.py`：`TaskEntry.edit_tools` 同步加入新編輯工具。
- Web UI：配置區新增 toolset 下拉選單；切換後只顯示該 toolset 的工具，預設全選，finish 固定必選；各 toolset 的勾選狀態會保留；請求會帶 `toolset`。
- 驗證：API 測試涵蓋未知 toolset、工具不在 toolset、只用新編輯工具、未指定 tools 時使用 toolset 全量；headless Chrome 確認 `repo-v2` 切換後顯示 14 個工具；全套件綠。

## 2026-11-26 ｜ 修 edit_lines 結尾換行、case_sensitive 驗證、移除 dead specs

- `edit_lines`：修正原始檔沒有結尾換行時，`new` 結尾的換行被 `splitlines()` 吃掉；現在會在編輯到最後一行時尊重 `new` 的結尾換行，同時保留原檔的換行慣例。
- `search_text` / `search_regex`：`case_sensitive` 現在必須是 bool；傳字串（例如 `"false"`）會回 tool error，不再被 truthy 當成 `True`。`search_text` 因此升到 v1。
- `TOOL_SPECS`：移除沒有 handler 的 `read_file`、`plan`、`run_tests`，同步移除 `agent_core.llm.PHASE_BY_TOOL` 的殘留條目與 catalog 測試。
- 測試：新增 `edit_lines` 無結尾換行回歸測試、`case_sensitive` 型別測試；tools 34 tests 綠，全套件綠。

## 2026-11-26 ｜ Sphinx 預設 toolset 改為 repo-v2

- `experiments/sphinx_7462/task.py` 與 `tasks/registry.py` 的 Sphinx 預設 toolset 改為 `repo-v2`（14 個工具）。
- `repo-v1` 保留為 6 工具的精簡對照；CLI/Web 仍可顯式選 `repo-v1`。
- 更新 `docs/interfaces.md` 的 repo toolset 說明與 SphinxTask 測試期望。
- 測試：`experiments` 37、`web` 23、`tools` 34 全綠。

