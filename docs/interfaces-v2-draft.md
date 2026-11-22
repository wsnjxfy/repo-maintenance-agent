# 架构与接口草案（v2 draft r2）

> 状态：**草案 r2，未落地** ｜ 记录日期：2026-11-22 ｜ 对应计划：`HJ10_plan_and_tasks.md` 的 C2 / C3
>
> **定位**：本文是重构的设计单一来源。它**不替换** `docs/interfaces.md`（v1）；v1 记录的 `Action`、trajectory、summary 契约继续有效，本文只新增/修订与「多环境、多工具、多评测」相关的部分。代码尚未按本文改动。评审通过后再逐段落地，并把变更补进 v1 的变更记录。
>
> **r2 修订摘要**：接受 review 的 G1–G4、G6–G8；G5 改为「绑定会执行代码的动作」；中立层收敛为单一 `contracts/`；补充 DeepSeek 预算吸收、strategy 接口、报告模板更新；修正 `label` ≠ `phase` 的错误指令；补 Docker baseline 与 bytes/newline。

## 0. 为什么要这份草案

两件事触发了架构调整：

1. **仓库里出现了两个 agent 回路。** 教学线是 `agent_core/agent.py` + `mvp/run_demo.py`（驱动 toy backend）；实验线是 `experiments/sphinx_7462/baseline.py`（自带 `ScriptedModel`/`DeepSeekModel` 和回路，直接驱动 `runtime/backends/repo.py` 的 `ToolRunner`）。后者之所以另起炉灶，是因为核心回路驱动不了真实 repo 环境（`run_demo.py` 直接拒绝 `repo-v1`，observation 格式也不兼容）。
2. **工具的描述与实现分散。** 描述在 `tools/specs.py`，实现混在 `runtime/backends/repo.py`（617 行，工具语义 + 执行环境 + 生命周期 + CLI 混居），阶段名又在 `agent_core/llm.py` 的 `PHASE_BY_TOOL` 里另有一份。

**本次重构的头号目标不是「tools 搬到哪」，而是：收敛成一个回路、一个模型适配器；工具只差「做什么」，环境只差「在哪里做」。** 只要核心回路还驱动不了真实环境，实验侧就会继续长出平行实现。

## 1. 五条变化轴与对应接口

设计以「计划里有哪些会变的维度」为准，每条轴都有计划内的第二个实现，才引入抽象；没有第二个实现的不抽象。

| 变化轴 | 计划依据 | 接口 | 现在的第一个实现 | 计划内的第二个实现 |
| --- | --- | --- | --- | --- |
| 工具 / 工具集 | C2；R3 把「工具接口」当研究维度 | `Tool`（spec + handler）、`Toolset` | repo 六件工具 | C2 的 `bash` / `git_diff` / 正则 `search` |
| agent 模式 | R2 条件 A/B；R3 上下文机制消融 | `Policy`、上下文/阶段策略 | 阶段预算策略（现于 `baseline.py`） | R3 的「移除该机制」消融组 |
| 环境 | 本机 → 容器（C3） | `Environment` | `LocalEnvironment` | `DockerEnvironment`（C3） |
| 评测 | 本机 pytest → 官方 harness；R4 | `Evaluator` | sphinx 本机评测 | SWE-bench 官方 harness |
| 任务来源 | 单题 → dev split（C4） | `Task` | sphinx-7462 | C4 固定的 dev 清单 |

## 2. 依赖方向（必须单向）

```text
contracts/                 # 谁都可以 import；contracts 自己不 import 任何项目内模块
  protocols.py             # Environment / Policy / Action / ToolSpec 协议
  protected.py             # 受保护路径规则
  patch.py                 # a/ b/ 路径解析、拒绝 rename/copy

run.py (orchestrator)
  ├─► agent_core   ──►  tools (specs / toolsets / handlers)
  │        │                 ▲ handler 以参数拿到 env，不 import runtime
  │        └─► Policy/strategy
  ├─► runtime      （Environment / session / workspace）──► contracts
  ├─► evaluation   （只有 orchestrator 在 agent 结束后才调用）──► contracts
  └─► experiments/tasks   （每题常数与准备）

mvp = 一个极小 fixture + scripted policy，跑在 LocalEnvironment 上，复用同一套 handler
```

硬规则：

- `contracts/` **不得 import 项目内任何模块**（只允许标准库）。
- `tools/` 不得 import `runtime/`；handler 通过函数参数拿到 `env`，类型注解从 `contracts/` 取。
- `agent_core/` 不得 import `runtime/`、`evaluation/`、`experiments/`。它只认识 `Policy`、`Action`、`tools` 的名字与 handler 调用约定。
- `evaluation/` 不得被 `agent_core/`、`tools/` import；评测材料（`test_patch`、`FAIL_TO_PASS`…）永不进入 agent 侧（计划 §7.5）。
- 实验/任务侧可以 import 上面任何一层，但上面各层不得 import 具体某个实验。

## 3. 统一 observation 契约（v2，破坏性）

这是让「一个回路驱动两种环境」成立的关键。当前两种格式不一致：toy 直接返回原始 dict、出错抛异常；repo 返回 `{"status": "ok"|"error", "result"/"error"}`。回路里还用工具名 `run_tests` 做特例判断。

**v2 约定：dispatch 永不为「工具级错误」抛异常，一律返回规范化信封。**

```python
# 成功
{"ok": True,  "tool": "read_lines", "result": {...}, "changed_paths": [...]}

# 工具级失败（可恢复：留在 history 供策略自我修正）
{"ok": False, "tool": "read_lines", "error": "ValueError: Path escapes the workspace",
 "kind": "tool"}

# 预算/墙钟拒绝（不可恢复，回路据此停止）
{"ok": False, "tool": "apply_patch", "error": "Workspace action limit reached",
 "kind": "budget"}
```

- `result` 的内部结构由各工具决定，**不冻结**。
- `kind ∈ {"tool", "budget", "timeout"}`；缺省视为 `"tool"`。
- `changed_paths`：非执行类 handler 必须回报（读/搜/补丁），供 session 做增量审计；执行类动作不需要。
- **`fatal` 标记**：handler 遇到「环境故障 / 测试加载失败 / 执行超时」时，在 observation 上设 `"fatal": True`；回路看到就停，不再按工具名判断。
- 回路只认 `ok`、`kind`、`fatal`，**不再按工具名做任何特例**。唯一例外是 `finish`——它是协议信号，不是普通工具。
- `finish` 的结果统一为：

```python
{"ok": True, "tool": "finish",
 "result": {"finished": True,
            "accepted": True | False | None,   # None = 交给独立评测
            "submission": {"patch": "...", "sha256": "...", "changes": [...]}}}
```

- 测试类结果（环境故障 / 超时 / 加载失败）作为 `result.status` 存在于 `run_visible_tests` 的 `result` 里；`fatal` 由 handler 根据 `result.status` 设置。

> 兼容：报告与分析脚本需同时能读 v1（顶层 `error` 键）与 v2（`ok`+`error`）。这是 v2 对 trajectory 的唯一破坏性改动，落地时 `versions.trajectory` 升到 v2。

## 4. Agent 回路契约

`Agent.run(policy, dispatch, trajectory_path) -> (history, status, stop_reason)`

- `dispatch` 由 orchestrator 提供（已绑定 env、toolset、session）；
- `env` 不直接传给 Agent——回路只消费 observation，不碰环境。

停止条件（全部与工具名无关，`finish` 除外）：

| 条件 | status |
| --- | --- |
| `policy.next_action` 抛异常 | `policy_failed` |
| policy 返回 `None` | `incomplete` |
| 连续 `ok=False` 且 `kind="tool"` 次数 ≥ `max_tool_errors` | `policy_failed` |
| `kind in {"budget", "timeout"}` | `budget_exhausted`（`timeout` 记入 stop_reason） |
| observation 带 `fatal=True` | `incomplete`（原因待查，不自动归因） |
| `tool == "finish"` 且 `result.finished` 且 `accepted is not False` | `completed` |
| `tool == "finish"` 且 `accepted is False` | `incomplete` |
| 步数用尽 | `budget_exhausted` |

> `accepted=None` 表示 backend 只交付、不判定；回路视为 `completed`，最终成败由评测层的 `resolved` 决定。

每步写一条 trajectory 事件，字段沿用 v1 §5（`step/phase/intent/action/observation/elapsed_ms` + 可选 `llm`）。

**预算单一化**：当前 `Agent.max_steps` 与 `ToolRunner` 的 `protocol.max_actions` 各管一套。v2 统一由 `Session`（§7）持有动作预算与墙钟；`Agent` 只持有「决策步数上限」。两者语义不同（一步决策可能不产生动作，如解析失败重试），分开记录：`summary.steps` 与 `summary.actions`。

## 5. 工具层：Tool = spec + handler

### 5.1 spec（`tools/specs.py`）

工具目录仍是**数据**。新增字段：

```python
TOOL_SPECS = {
    "read_lines": {
        "args": {"path": "str", "start": "int?", "end": "int?"},
        "doc": "读取文件的行区间。",
        "label": "读取行区间",        # 报告里的动作名
        "phase": "阅读上下文",        # 报告里的阶段名（不要用 label 代替）
        "mutating": False,
        "executes_code": False,      # True 的动作触发完整 manifest 审计
        "handler": "read_lines",     # HANDLERS 的键
    },
    "run_visible_tests": {
        "args": {"paths": "list[str]", "timeout_seconds": "float"},
        "doc": "运行指定 pytest 目标。",
        "label": "运行可见测试",
        "phase": "验证",
        "mutating": False,
        "executes_code": True,
        "handler": "run_visible_tests",
    },
    # ...
}
```

**`label` 与 `phase` 是两张表，不能互相派生。** 现在 `agent_core/llm.py` 的 `PHASE_BY_TOOL` 记录的是阶段（`阅读上下文`），spec 的 `label` 记录的是动作名（`阅读代码`）；把 `PHASE_BY_TOOL` 删掉后必须用 spec 的 `phase` 补上，否则报告会退化成 `label`。

### 5.2 handler（`tools/handlers.py`）

handler 只做**工具语义**：参数校验、受保护路径判断、结果整理。实际的读/写/执行/套补丁委派给 `env`。

```python
def read_lines(env, path, start=1, end=None):
    # 1. 参数校验（范围、类型）
    # 2. env.read_text(path) —— 越界/symlink 安全由 env 保证
    # 3. 整理成 result dict
    ...

HANDLERS = {"read_lines": read_lines, "search_text": search_text, ...}

def dispatch(name, env, settings, args):
    spec = settings.specs.get(name)
    if spec is None:
        return {"ok": False, "tool": name, "error": "unknown tool", "kind": "tool"}
    try:
        result = HANDLERS[spec["handler"]](env, **args)
        observation = {"ok": True, "tool": name, "result": result}
        if isinstance(result, dict) and "changed_paths" in result:
            observation["changed_paths"] = result.pop("changed_paths")
        if isinstance(result, dict) and result.pop("fatal", False):
            observation["fatal"] = True
        return observation
    except (ValueError, TypeError, OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "tool": name,
                "error": f"{type(exc).__name__}: {exc}", "kind": "tool"}
```

> 注册表用**函数对象**，不用字符串 import 路径——便于 grep、重构与类型检查。spec 里的 `handler` 值是注册表的键。

划界线（哪些属于 handler、哪些属于 env）：

| 例子 | 放哪 |
| --- | --- |
| `apply_patch` 只接受 `a/`、`b/` 前缀、拒绝测试与配置文件 | `tools/handlers.py` |
| 多文件补丁的快照与失败回滚（含新增/删除） | handler 用 `env.read_bytes`/`write_bytes`/`delete` 组合；原子性语义在 handler |
| `search_text` 的字面/正则匹配、limit、大小写 | `tools/handlers.py` |
| 读文件、列目录、跑子进程 | `env` 原语 |
| 动作预算、墙钟、finish 冻结/导出 | `runtime/session.py` |

### 5.3 toolset（`tools/toolsets.py`）

- 工具必须先在 `TOOL_SPECS` 出现，否则 `get_toolset` 拒绝（沿用 v1）。
- **决定（r2）：收敛成单一工具家族。** `toy` 用真实工具的子集跑在极小 fixture 上，删除 `map_repository`/`find_symbol`/`plan`/`replace_text`/`run_tests` 五个 toy 专属工具。`map_repository`/`find_symbol` 若日后作为检索研究，再以独立 toolset 加回。
- `"backend"` 字段移除；环境由 orchestrator 的配置选择（`--env local|docker`），toolset 只决定「可用哪些工具」。
- prompt 工具表、报告标签、dispatch 白名单仍全部由选中的 toolset 生成（沿用 v1 §3.2）。

## 6. 环境层：Environment 协议

`Environment` 决定「在哪里、用什么方式」。最小原语集：

```python
class Environment(Protocol):
    # 文本
    def read_text(self, path: str) -> str: ...
    def write_text(self, path: str, content: str) -> None: ...
    # 字节（补丁、二进制、行尾敏感）
    def read_bytes(self, path: str) -> bytes: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    # 文件系统
    def exists(self, path: str) -> bool: ...
    def delete(self, path: str) -> None: ...
    def list_files(self, subdir: str = ".") -> list[str]: ...
    # 执行
    def run(self, command: list[str], *, cwd: str = ".", timeout: float,
            env_extra: dict | None = None) -> dict: ...   # {returncode, stdout, stderr, timed_out}
    # 审计与交付
    def manifest(self) -> dict: ...                       # {relpath: sha256}
    def export_patch(self, baseline, destination: Path) -> None: ...
```

设计立场：

- `apply_patch`、`search_text`、`run_visible_tests` **不是** env 原语，而是 handler 用上面的原语组合出来的；`run_visible_tests` 通过 `env.run` 调 pytest。
- **路径安全是 env 的保证**：越界、`..`、symlink/reparse point、Windows 保留名，全部在 `LocalEnvironment` 内部拒绝；handler 不重复实现。
- `manifest()` 单列一个方法而非用 `list_files`+`read_text` 组合，是为了 `DockerEnvironment` 能用一条命令在容器内枚举+哈希（效率）。
- Windows 专属处理（`taskkill` 进程树、保留设备名、reparse point）**关在 `LocalEnvironment` 实现内**，不外泄到 handler 或工具层。
- **bytes/newline**：`apply_patch` 必须用 bytes 套用与还原，避免 Windows CRLF 被改成 LF、或二进制文件解码失败；纯读工具（`read_lines`/`search_text`）才用 text。
- **Docker 的 baseline**：`export_patch(baseline)` 需要 baseline 在容器内可见。决定（r2）：`prepare` 在 workspace 内建立**一个不含 remote 的 baseline commit**（空 repo 的首个 commit，没有父历史），容器内 `git diff <commit>` 即可；不采用跨主机/容器挂载的路径。

两个实现：

- `LocalEnvironment`（`runtime/local.py`）：现在；内容主要来自 `repo.py` 的 `_safe_path`/`_files`/`_process`/`_worker_env` 与 `_git`。
- `DockerEnvironment`（C3 新增）：`run` = `docker exec`，`read_text`/`manifest`/`export_patch` 走容器内命令。handler、session、回路都不改。

## 7. Session：run 生命周期

`runtime/session.py` 持有一次 run 的状态与预算，内容来自 `repo.py` 的 `ToolRunner` 外壳 + `activate_run` + `_finish` 的导出部分：

- phase：`prepared → active → finished`；`prepared` 下允许只读预检，不计预算、不启动时钟。
- 预算：动作计数、墙钟时限、单次测试超时。超限即拒绝新动作（`kind="budget"/"timeout"`）；`finish` 永远放行以冻结现场。
- **完整性审计（G5 修正版）**：
  - `spec["executes_code"] == True` 的动作（`run_visible_tests`、C2 的 `bash`）与 `finish`：动作前后做**完整 `env.manifest()`** 比对。
  - 其余动作（读/搜/补丁）：只比对 handler 回报的 `changed_paths`，不做全量扫描。
  - 理由：完整 manifest 的价值是抓「工具之外的代码执行」对受保护文件的篡改；只有会跑 repo 代码的动作才可能发生这种事。`apply_patch` 的原子性由 handler 的读/写/删除组合保证，不需要为此全扫。
- `finish`：通过 `env.export_patch(baseline)` 原子导出 `final.patch` 到 `submission/`，记录 `protected_violations` 与 `accepted`（本地评测为 `None`），判定交给评测，**不自带验收**。

> `finish` 的「语义」（组装 submission 内容）在 handler；「完整性审计 + 原子导出」在 session。这与 §5 的划界一致。

## 8. 评测层与任务层

- `evaluation/grading.py`：从 `experiments/sphinx_7462/grading.py` 移入，已无 sphinx 耦合。
- `evaluation/harness.py`：从 `evaluate.py` 抽出的通用评测流程（应用候选补丁 + 官方 test_patch、跑 pytest、判定）。sphinx 专属常数留在任务侧。
- `experiments/sphinx_7462/task.py`（新）：`TASK_ID`/`REPO`/`BASE_COMMIT`、`protocol()`、`instructions()`、`probe_environment()`。
- `experiments/sphinx_7462/prepare.py`：瘦身，通用的「解压/复制/baseline/manifest/git init」上移到 `runtime/workspace.py`。

**共用纯函数放 `contracts/`**（G4）：

- `contracts/protected.py`：受保护路径规则（`tests/`、`conftest.py`、`requirements*.txt`…），repo、session、evaluation 共用；
- `contracts/patch.py`：`a/`、`b/` 路径解析、拒绝 rename/copy，handler 与 evaluation 共用。

评测端保留独立的「执行」路径（独立性是目的），但「解析规则」共用，避免 agent 以为合法的补丁被评测端拒绝。

## 9. 目标文件结构

`★`=已存在留用 · `☆`=新建（多来自现有代码拆分）

```text
contracts/                # 中立层：谁都可以 import，自己不 import 项目内模块
├── protocols.py          # ☆ Environment / Policy / Action / ToolSpec
├── protected.py          # ☆ 受保护路径规则（唯一来源）
└── patch.py              # ☆ a/ b/ 解析、拒绝 rename/copy

agent_core/               # 大脑：一个回路、一个模型适配器、决策
├── agent.py              # ★ 唯一 Agent.run；去掉 run_tests 名称特例，改看 ok/kind/fatal
├── action.py             # ☆ Action + Policy 协议（从 agent.py 拆出）
├── action_parsing.py     # ★ JSON 抽取 / args 正规化
├── llm.py                # ★ 唯一 OpenAI 兼容适配器（吸收 baseline 的 DeepSeekModel 与预算）
├── policy.py             # ★ Policy 协议 + 阶段/转移策略（吸收 edit_phases/edit_transition）
├── report.py             # ★
└── report_template.html  # ★ 工具名 switch 需随工具收敛更新

tools/                    # 做什么、允许什么
├── specs.py              # ★ 加 handler/phase/executes_code；按 §5.3 收敛工具
├── toolsets.py           # ★ 移除 "backend" 字段
└── handlers.py           # ☆ 六件工具语义，操作委派 env

runtime/                  # 在哪里、用什么方式做
├── env.py                # ☆ Environment 协议（从 contracts 重导出）
├── local.py              # ☆ LocalEnvironment（路径安全 + git + process）
├── session.py            # ☆ 预算、时限、phase、审计、finish 冻结/导出
├── workspace.py          # ☆ 准备工作区（解压/复制/baseline/manifest/git init）
├── fsguard.py            # ☆ 纯函数 safe_path/files/changes/digest
├── process.py            # ☆ 子进程 + 进程树终止 + 超时
├── text_edit.py          # ★ 唯一片段替换
└── exec/                 # ★ 测试执行器（去留见 §12）

evaluation/               # 独立评测（只有 orchestrator 在 agent 结束后调用）
├── grading.py            # 从 experiments 移入
└── harness.py            # ☆ 通用评测流程

experiments/sphinx_7462/  # 单题粘合：只剩常数与准备
├── task.py               # ☆ TASK_ID/protocol/instructions/probe
├── prepare.py            # 瘦身
└── README.md

run.py                    # ☆ 通用编排（建 run 目录→Agent→评测→summary/report）

mvp/                      # 最小可用实例（R1）：越小越好
├── fixtures/tiny/        # ☆ 极小 fixture（1–2 文件 + 1 个可见测试）
├── scripted_policy.py    # ☆ 极小脚本策略（从 agent_core/policy.py 的 ScriptedPolicy 来）
├── demo.py               # ☆ 薄壳：LocalEnvironment 跑 tiny fixture，调 run.py
└── tests/test_core.py    # ☆ 精简核心回归
```

## 10. 与当前的差异（逐文件去向）

**收敛（headline）：两个回路 → 一个；两个模型适配器 → 一个；两个 backend → 一组 handler + 一个 Environment。**

| 现有文件 | 处置 |
| --- | --- |
| `runtime/backends/repo.py`（617） | 拆解删除：6 个 `_xxx`→`tools/handlers.py`；辅助→`fsguard`/`process`；生命周期→`session.py`；sphinx 探针→`experiments/task.py`；CLI 删或入 `runtime/cli` |
| `runtime/backends/toy.py`（112） | 删除（无独立 toy backend；toy 用真实 handler 的子集） |
| `runtime/backends/__init__.py`（`build_backend`） | 删除（无人调用） |
| `experiments/.../baseline.py`（525） | 拆解删除：模型→`llm.py`（**吸收预算机制**）；回路→`agent.py`；`edit_phases`/`edit_transition`→`agent_core/policy`；编排→`run.py`。**等垂直切片绿了再删** |
| `experiments/.../edit_phases.py`、`edit_transition.py` | 移入 `agent_core`（策略）；接口在 R3 定稿（见 §12） |
| `experiments/.../evaluate.py` | 拆分：通用→`evaluation/harness.py`；常数→`experiments/task.py` |
| `experiments/.../grading.py` | 移入 `evaluation/` |
| `experiments/.../prepare.py` | 瘦身：通用工作区准备→`runtime/workspace.py`；baseline commit 由 workspace 负责 |
| `agent_core/policy.py` 的 `ScriptedPolicy` | 移到 `mvp/scripted_policy.py` |
| `agent_core/llm.py` 的 `PHASE_BY_TOOL` | **阶段名移到 spec 的 `phase`**；不要用 `label` 代替（两者语义不同） |
| `mvp/run_demo.py` | 瘦身成 `mvp/demo.py` 薄壳，编排移到 `run.py` |
| `mvp/fixtures/timeout_demo/`（4 文件） | 缩小成 1–2 文件 tiny fixture |
| `mvp/tests/test_mvp.py`（**40 个测试 / 472 行**） | 拆分：LLM/解析测试→`agent_core/tests`；环境安全测试→`runtime/tests`；其余精简 |
| `runtime/tests/test_repo_backend.py`（**23 个测试 / 547 行**） | 重写成 `LocalEnvironment`+`session` 测试，保留安全案例（越界/受保护/回滚/超时/进程树） |
| `experiments/.../tests/test_action_parsing.py` | 移到 `agent_core/tests`（跨层错位修正） |
| `agent_core/action_parsing.py`、`runtime/text_edit.py` | 留用（已是对的方向） |
| `agent_core/report_template.html` | 工具名 `switch` 需在工具收敛后更新；未知工具回落 raw JSON |

## 11. 落地顺序（评审通过后）

0. **前置条件**：Sphinx 7462 的四个输入（`sphinx-base.tar.gz`、`record.json`、`review.json`、`reference.patch`）目前**不在本机**；垂直切片先用合成 repo（现有 `test_evaluate` / `test_repo_backend` 已有 fixture 可参考），等输入到了再换。
1. **先冻结本文**：两人对齐 §2 依赖方向、§3 observation 契约、§6 Environment 原语集（含 bytes/delete）。
2. **抽纯函数与中立层**：`contracts/`（protocols/protected/patch）、`runtime/fsguard.py`、`runtime/process.py`（低风险，对 repo 与 Docker 都有用）。
3. **垂直切片**：一个 Task（先合成 repo）、`LocalEnvironment`、3–4 个核心 handler、统一后的 `Agent` 回路、现有 sphinx 评测。把 `repo.py` 的安全逻辑移植过去。**`baseline.py` 保留并跑，直到切片能产出等价的 `resolved`。**
4. **Sphinx 切片**：输入到位后，用 sphinx-7462 重跑同一套；确认后删除 `baseline.py`。
5. **重写 toy**：用新接口做最小 MVP（tiny fixture + scripted policy），作为通用性验证。若重写 toy 需要改核心层，说明接口不够通用。
6. **C2**：在新结构上加 `bash`、`git_diff`、正则 `search`——每个是加一条 spec + 一个 handler；`bash` 的 spec 必须标 `executes_code: True`。
7. **C3**：新增 `DockerEnvironment` 与官方 harness 的 `Evaluator`；baseline 用 §6 的首次 commit 方案。

## 12. 待定（需拍板）

1. **MVP 测试执行用 pytest 还是 stdlib unittest？** 决定：**pytest 统一**（删 `runtime/exec/`，MVP 与 repo 同一条测试路径）；代价是失去「零依赖」演示特性（CI 本就装 pytest）。
2. **工具集是否收敛成单一家族？** 决定：**收敛**，删 5 个 toy 专属工具；`map_repository`/`find_symbol` 日后有研究需要再以独立 toolset 加回。
3. **`run.py` 的位置/命名**：决定：**根目录 `run.py`**（唯一同时 import 所有层的入口）。
4. **strategy 接口（Policy 如何消费 `EditPhases`/`EditTransition`）**：**R3 时定稿**；v2 的 observation 需保留它们依赖的字段（`result.status`、`changed_paths`、`kind`）。
5. **Docker baseline 形态**：决定：workspace 内首个 baseline commit（无 remote），`export_patch` 用 `git diff <commit>`；不跨容器挂载。

## 13. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-11-22 | v2 draft | 初稿：五条变化轴与接口、单向依赖、统一 observation 契约、Environment/Session 协议、目标结构与逐文件差异、落地顺序；收敛两个回路为一个 |
| 2026-11-22 | v2 draft r2 | 接受 review：补 `exists/delete/read_bytes/write_bytes`、`export_patch`；`fatal`/`kind` 取代 `run_tests` 名称特例；G5 改为「绑定 `executes_code` 的动作 + finish」；中立层收敛为 `contracts/`；`label`/`phase` 分离；补 Docker baseline、DeepSeek 预算吸收、strategy 接口 R3 定稿、报告模板更新；修正测试数（23/40） |
