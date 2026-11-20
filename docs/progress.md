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

