# 接口与记录约定（v1）

> 状态：v1 草案 ｜ 记录日期：2026-11-01 ｜ 对应计划：`HJ10_plan_and_tasks.md` 的 C0
>
> **定位**：本文件是 `Action`、工具目录、`trajectory`、`summary` 四份契约的单一来源。代码、prompt、报告、测试、实验分析都以本文件为准；出现分歧时先改本文件，再改代码。

## 0. 为什么要冻结

这三样东西被 5 个模块同时消费：

```text
Action schema    ← prompt（模型按它输出）
                 ← agent.py（解析与执行）
                 ← report（显示）
工具目录          ← prompt（工具说明）
                 ← tools.dispatch（真实现）
                 ← report 标签 + 测试断言
trajectory 字段   ← agent.py（写）
                 ← report（读）
                 ← 实验分析脚本（读）
                 ← test_mvp.py（断言）
```

改一个，五处要同步。两个人各自改，问题不是 merge 冲突（那还是可见的），而是**契约改了一半、另一半没跟上**的无声漂移。

**冻结的含义**：现阶段只有一份定义；任何改动都要有版本号、有同步、有记录。不是永远不能改。

## 1. 版本字段

每次 run 的 `summary.json` 必须记录：

```json
"versions": {"action": "v1", "tools": "v1", "prompt": "v1", "trajectory": "v1"}
```

规则：

- 兼容性改动（只加可选字段）：版本号不变。
- 破坏性改动（改名、删字段、改语义）：对应版本号 +1，并在 §9 的变更记录里写一行。
- 报告与实验分析必须能读所有历史版本；这是唯一"永不改变"的要求。

## 2. Action schema v1

### 2.1 形状

```python
@dataclass
class Action:
    tool: str
    args: dict = field(default_factory=dict)
    phase: str = ""     # 可选元数据
    intent: str = ""    # 可选元数据
```

最小合法 Action 只有两个字段：

```json
{"tool": "run_tests", "args": {}}
```

### 2.2 谁填 phase / intent

**v1 决定：模型只输出 `tool` 与 `args`；`phase` 与 `intent` 由适配器按工具名映射生成。**

理由：

- 少两个输出字段，对 8B 模型意味着更少的格式错误与更低的输出 token 成本。
- 报告仍然能显示"这一步在做什么"，因为映射表由我们控制。

脚本策略（`ScriptedPolicy`）保留自行填写 `phase`/`intent` 的能力，用于教学演示的可读性。

### 2.3 规范化层（必须存在）

模型的原生输出**不得**直接进入控制器。适配器负责归一化：

```text
OpenAI tool_calls:
  {"name": "replace_text", "arguments": "{\"path\": \"config.py\", ...}"}
        │  arguments 是字符串，要 json.loads
        ▼
内部 Action:
  {"tool": "replace_text", "args": {"path": "config.py", ...}, "phase": "...", "intent": ""}

文字协议（本地小模型备用）:
  <tool>replace_text</tool><args>{"path": "config.py"}</args>
        ▼
同样归一化为 Action
```

归一化失败（JSON 坏、工具名不存在、参数类型错）时：**返回一条 `error` observation 让模型自我修正，不要抛异常终止整个 run**。这本身是可观察的行为，应记录进 trajectory。

### 2.4 兼容规则

| 改动 | 性质 | 处理 |
| --- | --- | --- |
| 新增可选字段 | 兼容 | 消费者忽略未知字段 |
| `phase`/`intent` 改由模型提供 | 兼容 | 适配器优先用模型值，缺失则映射 |
| `args` 改名 `arguments` | 破坏 | `action: v2`，旧数据读取时映射 |
| 删除字段 | 破坏 | 同上 |

## 3. 工具目录（TOOL_SPECS）

### 3.1 结构

工具目录是**数据**，不是逻辑。逻辑放各工具的实现里。

```python
# tools/specs.py（当前可先放在 tools.py 顶部）
TOOL_SPECS = {
    "read_file": {
        "args": {"path": "str", "start": "int?", "end": "int?"},
        "doc": "读取 workspace 内文件，可选行区间",
        "mutating": False,
    },
    "replace_text": {
        "args": {"path": "str", "old": "str", "new": "str"},
        "doc": "在文件中做唯一片段替换；old 必须恰好出现一次",
        "mutating": True,
    },
    # ...
}
```

### 3.2 四个消费者必须由 TOOL_SPECS 生成

| 消费者 | 内容 | 生成方式 |
| --- | --- | --- |
| prompt 工具表 | 名称、说明、参数 | 遍历 `TOOL_SPECS` |
| `Tools.dispatch` | 合法工具集合 | `set(TOOL_SPECS)` |
| 报告标签 | 中文名称 | `TOOL_SPECS[name]["label"]`，缺失回落原名 |
| 测试 | 工具集合一致性 | 比较三处集合是否相等 |

**禁止手写 prompt 里的工具表**——那是"加了工具但模型不知道"的主要来源。

### 3.3 tools v1（当前）

| 工具 | 参数 | 说明 | 只读 |
| --- | --- | --- | --- |
| `map_repository` | — | AST 结构概览 | ✓ |
| `find_symbol` | `symbol` | 定义与文本候选引用 | ✓ |
| `read_file` | `path` | 读文件（v1 无行区间） | ✓ |
| `plan` | `steps[]` | 记录计划 | ✓ |
| `replace_text` | `path, old, new` | 唯一片段替换 | |
| `run_tests` | — | 固定跑可见测试 | |
| `finish` | `reason` | 提交完成，需最新测试通过 | |

### 3.4 tools v2（C2 目标，未实现）

`bash`、`read_file`（含行区间）、`search`（正则、结果上限）、`edit`（由 `replace_text` 改名）、`git_diff`、`run_tests`、`finish`。

待定：`map_repository` 与 `find_symbol` 保留还是并入 `search`（与 R3 的工具接口实验一起决定）。

## 4. 两人协作规则

### 4.1 单写者

**同一时间只有一个人改工具目录与 `tools.py`。** 另一人通过 issue 或 PR 描述需求，由单写者排进版本，不直接改。

### 4.2 变更窗口

跑对照实验期间工具目录冻结。新需求记下来，等一轮结束再升版本。这与"固定模型与预算"是同一种纪律。

### 4.3 结构上减少冲突

- 工具实现拆成独立文件（`tools/bash.py` 等），目录集中在 `TOOL_SPECS`；
- 每人只新增自己的工具文件与测试，不动别人的行；
- 合并前跑 §8 的守门测试。

### 4.4 分工

```text
A：tools 目录 / prompt / 适配器   ← 工具契约的唯一写者
B：数据集 / Docker / harness      ← 不碰 agent 工具
共同：本文件（改动走 PR）
```

## 5. trajectory schema v1

每一行一个事件，追加写入 `trajectory.jsonl`。

### 5.1 必填字段

```json
{"step": 1,
 "phase": "认识仓库",
 "intent": "先找到生产文件、测试和主要函数。",
 "action": {"tool": "map_repository", "args": {}},
 "observation": {"files": []},
 "elapsed_ms": 12}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `step` | int | 从 1 开始 |
| `phase` | str | 阶段名（可由映射生成，可为空串） |
| `intent` | str | 意图说明（可为空串） |
| `action` | object | 必须含 `tool` 与 `args` |
| `observation` | object | 工具返回值；错误时含 `error` |
| `elapsed_ms` | int | 墙钟耗时 |

### 5.2 可选字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `llm` | object | `{model, prompt_tokens, completion_tokens, cost_usd, attempts, last_error}`；`attempts` 含重试次数，`last_error` 为最后一次解析失败原因（无失败则为 null） |

### 5.3 兼容规则

- **只允许加可选字段**，不得改必填字段名或删字段。
- 新字段必须能缺失，读取方要给默认值。
- 旧文件必须保持可读——分析脚本与报告都要容忍 v1 记录。
- `observation` 的结构由各工具决定，**不冻结**；只有 `error` 键的语义固定（存在即失败）。

### 5.4 LLM 调用日志（可选）

模型运行时默认写 `llm_calls.jsonl`，**每次 API 调用一行**（含重试与失败），用于复盘「模型看到什么、回了什么」。`--no-llm-log` 可关闭。

```json
{"step": 6, "attempt": 1, "model": "deepseek-flash",
 "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
 "raw_content": "好的，我先看 config.py …",
 "usage": {"prompt_tokens": 1400, "completion_tokens": 400},
 "error": "JSONDecodeError: Expecting value: line 1 column 1 (char 0)",
 "elapsed_ms": 3200}
```

| 字段 | 说明 |
| --- | --- |
| `step` | 对应的轨迹步号（同一步的重试序号相同） |
| `attempt` | 该步第几次尝试 |
| `messages` | 本次实际送出的完整消息（system + user） |
| `raw_content` | 模型原始输出；失败时为原始文本，成功时为 JSON 文本 |
| `error` | 解析失败原因；成功为 null |
| `elapsed_ms` | 本次调用墙钟耗时 |

该文件不进入报告与 summary 的评测，只用于调试、重试归因与案例附录。文件较大时用 `--no-llm-log` 关闭。

## 6. summary schema v1

在现有字段基础上扩展，必填：

```json
{"run_id": "...", "instance_id": "...", "model": "...", "max_steps": 24,
 "versions": {"action": "v1", "tools": "v1", "prompt": "v1", "trajectory": "v1"},
 "status": "...", "stop_reason": "...", "steps": 17,
 "model_calls": 0, "tokens": {"prompt": 0, "completion": 0}, "cost_usd": 0.0,
 "elapsed_seconds": 0.0, "test_calls": 3,
 "evaluation": {"resolved": false, "fail_to_pass": [], "pass_to_pass": [],
                "new_regressions": []},
 "failure_category": "requirement_failed"}
```

`failure_category` 取值：

`requirement_failed` ｜ `regression` ｜ `tool_or_policy_error` ｜ `budget_exhausted` ｜ `environment_error`

教学 MVP 阶段 `instance_id` 可为空、`resolved` 可为 `false`。

## 7. 破坏性变更检查清单

以 `replace_text → edit` 为例：

- [ ] 更新 `TOOL_SPECS`（新增 `edit`，备注旧名）
- [ ] 在归一化层加旧名映射：`replace_text → edit`（保证旧 prompt／旧轨迹仍可执行／可读）
- [ ] 更新 prompt 工具表（若为生成式，无需手改，但要重跑生成）
- [ ] 更新报告标签映射
- [ ] 更新测试断言的工具名
- [ ] 升 `tools` 版本号，并在 §9 记录
- [ ] 用一条旧 trajectory 验证仍可渲染
- [ ] 用一条新 run 验证 `edit` 可被调用

## 8. 守门测试

至少三条，放 `mvp/tests/`：

```python
# 1) trajectory 结构
REQUIRED = {"step", "phase", "intent", "action", "observation", "elapsed_ms"}

def test_trajectory_events_have_required_fields(events):
    for event in events:
        assert REQUIRED <= set(event)
        assert {"tool", "args"} <= set(event["action"])

# 2) 工具集合三处一致
def test_tool_catalog_matches_dispatch_and_prompt(tools, prompt_text, report_module):
    assert set(TOOL_SPECS) == set(tools.allowed_tools)
    assert set(TOOL_SPECS) == report_module.labels().keys()
    assert all(name in prompt_text for name in TOOL_SPECS)

# 3) 归一化失败不中断 run
def test_bad_tool_call_becomes_error_observation(adapter):
    action = adapter.normalize({"name": "nope", "arguments": "{}"})
    assert action is None or action.tool == "nope"  # 由控制器转成 error observation
```

测试 2 是防漂移的核心：它保证"加了工具但 prompt／报告没跟上"无法通过 CI。

以上三条已于 2026-11-01 落地：`mvp/tests/test_mvp.py` 的 `TestInterfaceContract`（共 4 条，含版本字段校验）。

## 9. 变更记录

| 日期 | 版本 | 变更 | 影响 |
| --- | --- | --- | --- |
| 2026-11-01 | v1 | 初稿：Action、工具目录、trajectory、summary 四份契约；确定模型只输出 tool/args | 建立基线 |
| 2026-11-01 | v1 | 落地：`TOOL_SPECS`、`tool_labels()`、`tool_table()`；报告标签与 `dispatch` 改为由目录派生；`summary` 增加 `versions`；守门测试 4 条 | 行为不变，契约可执行 |
| 2026-11-02 | v1 | `llm` 字段增加 `attempts`、`last_error`，用于定位重试原因 | 兼容（只加可选字段） |
| 2026-11-02 | v1 | 新增可选文件 `llm_calls.jsonl`：每次 API 调用的 messages / raw_content / error | 兼容（新文件，默认开启） |

## 10. 常见事故（本文件要防的东西）

| 事故 | 症状 | 防线 |
| --- | --- | --- |
| 模型输出不存在的工具 | `Unknown tool`，整轮失败 | 归一化前置校验 + prompt 由目录生成 |
| 加了工具但 prompt 没更新 | 模型永远不调用新工具 | 守门测试 2 |
| 改名未同步报告 | 报告显示"未记录操作" | 破坏性变更清单 |
| trajectory 字段各写一套 | 分析脚本 KeyError 或静默算 0 | §5 必填字段 + 测试 1 |
| 模型原生 `tool_calls` 漏进控制器 | 到处 `arguments` 字符串解析 | 强制归一化层（§2.3） |
