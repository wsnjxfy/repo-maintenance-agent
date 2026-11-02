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
