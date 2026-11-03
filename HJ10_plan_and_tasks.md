# HJ10 执行计划与任务清单

> 状态：草案 v0.1 ｜ 记录日期：2026-11-01 ｜ 适用仓库：`repo-maintenance-agent`
>
> **定位**：本文是执行的单一来源。README、`docs/mvp-decisions.md`、`HJ10-项目理解与技术路径.md` 里三份重复的"下一步"以本文为准，其余文档保留历史记录与设计理由。

## 0. 一页速览

| 项目 | 结论 |
| --- | --- |
| 最终目标 | 固定 LLM 与推理预算，比较不同 agent scaffolding 对仓库维护成功率的影响 |
| 任务来源 | SWE-bench；先抽 dev split 调方法，报告用剩余部分 |
| 最终模型 | Qwen3-8B（本地部署，量化设置待定并固定） |
| 阶段模型 | DeepSeek API（OpenAI 兼容），只用于验证 scaffolding，**不作为研究结论** |
| 语言范围 | Python |
| 参照实现 | mini-SWE-agent 的循环结构（LLM + 工具调用 + 步数控制） |
| 当前进度 | MVP 0 已完成；C0 已完成；**C1 已完成（连续 3/3，prompt v2）**；下一步 C2；详情见 [docs/progress.md](docs/progress.md) |
| 第一个可交付 | 1 个真实 SWE-bench instance 由 agent 产出补丁，并被官方 harness 判为 resolved 或 unresolved |
| 参与者 | 2 人 |

## 1. 为什么现在需要这份文档

1. **三处"下一步"已漂移**：`README.md` 说具体模型未定，`docs/mvp-decisions.md` 说数据集未选定，而研究目标已确定为 Qwen3-8B 与 SWE-bench。两人协同时会各照一份。
2. **现有分解是研究阶段（周～月），缺可勾选的工程任务**：没有任何一份文档回答"这周做完什么算完成"。
3. **接口必须先冻结**：`Action` schema、工具名、trajectory 字段一旦两人各改一套，merge 必然冲突。

## 2. 已定案前提

| 项 | 决定 | 说明 |
| --- | --- | --- |
| 任务来源 | SWE-bench（Lite 起步） | 真实 GitHub issue + 官方 Docker 化评测 |
| 阶段模型 | DeepSeek API | 目的：先证明 scaffolding 正确，再换小模型 |
| 最终模型 | Qwen3-8B | 版本、量化、生成参数固定后再比较设计 |
| 语言范围 | Python | 与 SWE-bench 主体一致 |
| 参照架构 | mini-SWE-agent | 单循环 + 工具调用 + 步数预算 |
| 预算口径 | token + 步数 + 测试调用 + 耗时 | 四维分别记录，不只记 token |
| 失败分类 | 需求失败／回归／工具或策略错误／预算耗尽／环境故障 | 五类，必须落进 summary |
| 开发／测试隔离 | 按仓库划分，不给隐藏测试反馈 | 避免同 repo 同时在 dev 与 test |

**明确不做的**：训练模型、改 SWE-bench 官方评测逻辑、在 Lite 上调 prompt 之后又用 Lite 报告成绩。

## 3. 两条路线及其对应关系

研究逻辑（R）回答"要研究什么"；执行检查点（C）回答"这周先做什么"。两者不是替代关系。

```text
R1 接模型 ──► C1 强模型跑通教学任务 ──► C2 工具泛化
   │
R2 开发集诊断 ◄── C3 单 instance 端到端 ◄──┘
   │            └──► C4 扩到 3–5 题 + 固定 dev split
   ▼
R3 单一机制改进（上下文选择优先）
   ▼
R4 独立测试集与多次实验
```

| 研究路线 | 对应执行检查点 | 差别 |
| --- | --- | --- |
| R1 接真实模型 | C1 + C2 | 本文用强模型先做，再降级 8B，便于归因 |
| R2 开发集诊断 | C3 + C4 | 没有 C3/C4 就没有可诊断的基线 |
| R3 单机制改进 | C5（未展开） | 依赖 C4 的结果 |
| R4 独立测试与多次实验 | C6（未展开） | 依赖 R3 选定的机制 |

## 4. 研究路线 R1–R4

### R1 用真实模型实现同一决策接口

- 做法：`Policy.next_action(history)` 由模型适配器实现，工具与链路结构保持不变。
- 完成定义：同一批任务上，模型只通过工具与 history 交互；`model_calls > 0`；token 与费用可计。
- 产出：适配器、prompt 模板（带版本号）、成本表。

### R2 开发集诊断

- 做法：同一模型、同一预算下比较两种上下文条件：
  - A：普通 agent（自行检索上下文）；
  - B：人工提供"相关修改前代码片段"，但不给修法、参考补丁或隐藏测试。
- 判读：若 B 明显更好，瓶颈在上下文选择；若两者都失败，先查模型理解与任务歧义。
- 完成定义：dev split 上两个条件各跑完，结果与上下文成本都落盘。
- 纪律：该诊断不是性能上界；不得用隐藏测试反馈调参。

### R3 单一机制改进

- 做法：只改一个机制（优先上下文选择／符号关系检索），固定模型与预算，做"移除该机制"的消融。
- 完成定义：对照组与实验组在同一 dev split 上完成，差异可归因。

### R4 独立测试集与多次实验

- 做法：按仓库划分 dev/test；每组多次运行；报告 resolved 率、回归、成本与耗时。
- 完成定义：结果表可复现，失败案例有人工分析。

## 5. 执行检查点 C0–C4

### C0 版控、环境与接口约定（0.5 天）

**状态（2026-11-03）：已完成** —— 接口已冻结，`.env`、分支与 `.venv` 均已建立并推送。

目标：让两人在同一约定下开工，避免改同一文件。

- [x] 在 `main` 提交计划与文档（`HJ10_plan_and_tasks.md` + `docs/`）
- [x] 建个人分支 `charles` 并推送（约定：每次 PR 只动一层）
- [x] 建 `.env` 与 `.env.example`；确认 `.gitignore` 覆盖 `.venv/`、`preds/`、`logs/`、`mvp/runs/`
- [x] 冻结接口草案：[docs/interfaces.md](docs/interfaces.md) v1（`Action`、工具目录、trajectory、summary）
- [x] 写入分工：A 负责适配器、prompt、工具层；B 负责数据集、Docker 环境、harness
- [x] 用 `uv venv .venv` 建独立环境（uv 0.9.9 / Python 3.11.15；`python` 已解析到 `.venv\Scripts\python.exe`）

完成定义：新机器 clone 后能一条命令复现教学 demo；接口草案评审通过。
负责人：两人共同。

### C1 强模型跑通现有教学任务（1–2 天，负责人 A）

**状态（2026-11-03）：已完成（连续 3/3）。**
证据：`demo-20261103-140044-fcd5d0`、`demo-20261103-140326-33aa60`、`demo-20261103-140345-d45d7a`（deepseek-flash，prompt v2，三次均 7/7 + 3/3）。

目标：把"模型会不会用工具"与"环境"彻底解耦。现有 fixture 只有 4 个文件、测试 15 秒跑完，一轮迭代约 30 秒，是最便宜的调试场。

- [x] 写 OpenAI 兼容适配器（`base_url`、模型名、temperature、max_tokens 全部配置化；现为 `LLM_*` 环境变量）
- [x] `history → prompt → tool JSON → Action` 的正规化层（文字协议；支持 ```json 围栏与散文包裹）
- [x] 每次调用记录 prompt/completion token、费用与耗时，写入 trajectory（另有 `llm_calls.jsonl` 记录完整请求/响应）
- [x] 在 `mvp/fixtures/timeout_demo` 上跑，目标：模型自主完成"读码 → 改两档 → 跑测试 → 修错 → 通过"
- [x] 连续 3 次成功且轨迹可重放（3/3：10–11 步、32 次调用对 31 步，仅 1 次重试）
- [x] `summary.json` 的 `model_calls`、`tokens` 不再是 0 / null

完成定义：8/8 可见测试由模型自主达成（不是脚本）；`final.patch` 与轨迹一致；费用可见。
纪律：隐藏测试仍只在 `Agent.run` 结束后执行；不得进入 history。
风险：模型可能不遵守"只改两个文件"的约定——这本身就是有用的观察，记录下来。

### C2 工具泛化（2–3 天，负责人 A）

目标：工具能作用在任意 workspace，而不是 4 个硬编码文件名。

- [ ] 去掉文件名白名单，改为"workspace 内任意路径 + 越界与 symlink 检查"
- [ ] 新增 `bash`（超时、输出截断、工作目录限制）
- [ ] `read_file` 支持行区间；`search` 支持正则与结果上限
- [ ] `edit` 保持唯一片段替换 + 语法检查；新增 `git diff`
- [ ] 保留"测试文件只读"策略
- [ ] 单测覆盖：越界、超长输出、超时、编辑冲突、并发修改

完成定义：在一个真实 repo 副本上，定位 → 修改 → 跑该 repo 的测试能走通，且无越界写入。
风险：`bash` 让"非 OS 沙箱"的弱点放大；必须在容器内跑，见 C3。

### C3 单个 SWE-bench instance 端到端（1–2 天，负责人 B）

目标：打通"instance → agent 补丁 → 官方判定"。

- [ ] 修好 Docker（WSL2 后端或云端方案，见 §8）
- [ ] 拉取数据集，按 §7.1 标准选 1 个 instance
- [ ] 先跑 gold 自检：`swebench eval verified --gold -i <instance_id> --run-id validate-gold`
- [ ] 启动该 instance 的容器，agent 在容器内工作（`docker exec` 或 SWE-ReX）
- [ ] 产出 predictions JSONL，交官方 harness 判定
- [ ] `summary.json` 记录 resolved 与失败分类

完成定义：一条命令从 `instance_id` 到 resolved/unresolved 全自动；环境故障与 agent 失败可区分。
风险：环境建置通常是最大时间黑洞；gold 跑不过就是环境问题，不要先怀疑 agent。

### C4 扩到 3–5 题并固定 dev split（1 天，两人共同）

- [ ] 按 §7.2 抽出并固定 dev 清单（记录随机种子）
- [ ] 批量脚本先跑 3–5 题
- [ ] 汇总表：resolved、步数、token、费用、耗时
- [ ] 人工归因一次失败案例

完成定义：批量脚本可重跑；结果表与成本表落盘；dev 清单进入版本控制。
提醒：3–5 题只用于验证管线，不用于声称成功率。

### C5 / C6（待 C4 后展开）

- C5：R3 的单机制消融（固定模型与预算，只切换一个机制）。
- C6：R4 的独立测试集与多次实验（按仓库划分 test，报告 resolved 率与回归）。

## 6. 接口与记录约定（草案，C0 定稿）

> 细则已独立成文：[docs/interfaces.md](docs/interfaces.md)（v1，单一来源）。本节只保留摘要。

### 6.1 Action

```python
Action(tool: str, args: dict, phase: str = "", intent: str = "")
```

### 6.2 工具清单（目标形态）

| 工具 | 作用 | 关键约束 |
| --- | --- | --- |
| `bash` | 执行命令 | 超时、输出截断、cwd 限定 workspace |
| `read_file` | 读文件 | 支持行区间，超长截断 |
| `search` | 文本／正则检索 | 结果上限，返回行号 |
| `edit` | 唯一片段替换 | 语法检查、失败不改文件 |
| `git_diff` | 查看当前改动 | 只读 |
| `run_tests` | 跑测试 | 子进程、超时、结构化结果 |
| `finish` | 提交完成 | 需最新一次测试通过 |

待定：现有 `map_repository`、`find_symbol` 是否保留为独立工具，或并入 `search`（影响"工具接口"这一研究维度，需与 R3 一起决定）。

### 6.3 trajectory 每步字段

```json
{"step": 1, "phase": "...", "intent": "...",
 "action": {"tool": "...", "args": {}},
 "observation": {},
 "elapsed_ms": 0,
 "llm": {"model": "...", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}}
```

### 6.4 summary 字段（在现有基础上扩展）

必填：`run_id`、`instance_id`、`model`、`max_steps`、`status`、`stop_reason`、`steps`、`model_calls`、`tokens{prompt,completion}`、`cost_usd`、`elapsed_seconds`、`test_calls`、`evaluation{resolved, fail_to_pass, pass_to_pass, new_regressions}`、`failure_category`。

### 6.5 失败分类

`requirement_failed` ｜ `regression` ｜ `tool_or_policy_error` ｜ `budget_exhausted` ｜ `environment_error`

### 6.6 预算记录

四维分开记：累计输入／输出 token、步数、测试调用次数、墙钟耗时。相同上下文窗口不等于相同总预算。

## 7. 数据集与评测协议

### 7.1 instance 选择标准（第一轮）

- `patch` 只改 1–2 个文件；
- `FAIL_TO_PASS` 不超过 3 个；
- 仓库体量中等（如 flask、requests、pytest 这类优于 matplotlib、sympy）；
- 优先选依赖单一、构造成本低的。

### 7.2 dev / test 划分

- 按**仓库**划分，而不是随机按 instance 划分；
- dev 15–20 题，固定清单与抽样种子并入库；
- 剩余部分才用于报告；
- 禁止：在 dev 上调 prompt 后用同一批数据报告成绩。

### 7.3 环境自检

先跑 gold 补丁。gold 不通过 = 环境问题，不进入 agent 调试。

### 7.4 predictions 与判定

```json
{"instance_id": "...", "model_name_or_path": "qwen3-8b+hj10-v0", "model_patch": "diff --git ..."}
```

判定口径：`FAIL_TO_PASS` 全过且 `PASS_TO_PASS` 全过才算 resolved。

### 7.5 卫生规则

- `patch`、`test_patch`、`FAIL_TO_PASS`、`PASS_TO_PASS` 只进评测端，永不进 prompt 或工具输出；
- 同一个 `run_id` 会命中结果缓存，重测必须换 `run_id`；
- 评测日志与预测文件不入库（`.gitignore` 覆盖）。

## 8. 环境现状（2026-11-01 实测）与处置

| 项 | 现状 | 处置 |
| --- | --- | --- |
| Docker CLI | 已安装 29.2.1 | — |
| Docker daemon | **未运行**（`npipe:////./pipe/docker_engine` 连不上） | 启动 Docker Desktop |
| WSL | `wsl --status` 返回 `E_ACCESSDENIED` | 安装／修复 WSL2 发行版，或改用云端评测 |
| 磁盘 | 官方建议 ≥120GB 空闲给 Docker 虚拟磁盘 | 先确认剩余空间 |
| 内存／CPU | 官方建议 16GB RAM、8 核 | 先确认本机规格 |
| Python | `.venv` 已建立并生效（uv 0.9.9 / Python 3.11.15；未装 pip） | 装包用 `uv pip install --python .venv\Scripts\python.exe` |
| 已知坑 | 在 DSH 沙箱下直接跑 `unittest` 会因临时目录权限全部报错 | 属环境问题，不是代码缺陷；CI 不受影响 |

处置顺序：先 WSL → 再 Docker → 再 120GB → 最后才装 `swebench`。若 WSL 长期不可用，退路是云端评测（sb-cli / Modal），本机只跑 agent。

## 9. 成本控制

- 设 `max_steps`（首轮 50）与单任务 token 上限；超限即停，状态记 `budget_exhausted`。
- DeepSeek 单价低，5 个 instance × 50 步预计在数美元内，但必须实测后写回本文档。
- 每次运行都落 `cost_usd`；汇总表按 instance 与整体两栏统计。
- 不在评测机上重跑相同 `run_id` 的缓存结果。

## 10. 风险清单

| 风险 | 影响 | 概率 | 缓解 |
| --- | --- | --- | --- |
| 环境建置耗时失控 | 高 | 高 | 先跑 gold；用预建镜像；必要时云端评测 |
| Windows + Docker 效能／I/O 慢 | 中 | 中 | image 预先 build；减少并发 worker |
| Lite 被当作调参集 | 高（结论失效） | 中 | 立即固定 dev split |
| 上下文爆炸导致成本上升 | 中 | 高 | 工具输出截断；history 摘要；token 上限 |
| `bash` 放大安全边界问题 | 高 | 中 | 只在容器内跑；网络与路径限制 |
| 两人同时改同一层 | 中 | 中 | 分层 PR；接口先冻结 |
| 强模型结果被误当成最终结论 | 高 | 中 | 所有 summary 标注 model；报告只用 8B 结果 |

## 11. 待确认事项

- [ ] 课程评分细则、截止日期、交付形式
- [ ] 总预算上限（API 费用与时间）
- [ ] 本地硬件实测（内存、可用磁盘、CPU 核数）
- [ ] dev split 规模（15 或 20）
- [ ] 是否引入 SWE-ReX（还是自己封 `docker exec`）
- [ ] 是否保留 `map_repository` / `find_symbol` 作为独立工具
- [ ] Lite 的题量与仓库分布（以 `datasets` 实际输出为准）

## 12. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-11-01 | v0.1 | 初稿：合并三处"下一步"，确定 R1–R4 与 C0–C4，记录环境实测 |

## 13. 后续需要同步的文档（本次未改动）

- `README.md`：把"后续按小步叠加"改为指向本文，并删除"具体模型尚未确定"的过时表述。
- `docs/mvp-decisions.md`：更新"待确认"中已定案的部分（模型、数据集、团队人数）。
- `HJ10-项目理解与技术路径.md`：第 64 行"三人分工"改为两人及角色。
