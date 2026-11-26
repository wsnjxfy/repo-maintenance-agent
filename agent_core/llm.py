"""OpenAI-compatible chat adapter for the teaching controller (C1).

Works with any endpoint that speaks the OpenAI ``/chat/completions`` protocol:
OpenAI, DeepSeek, Moonshot, Qwen (DashScope compatible mode), OpenRouter,
Groq, Together, Azure-style gateways, and local servers such as Ollama or
vLLM. Only ``LLM_BASE_URL`` / ``LLM_MODEL`` / ``LLM_API_KEY`` need to change.

Dependency-free on purpose: ``urllib`` for HTTP, ``json`` for payloads, and a
local ``.env`` reader, so the repository keeps its "standard library only"
property. The transport is injectable so tests never touch the network.

Scope: this is the C1 minimum. It records per-step usage, retries once when the
model returns something unparsable, and rejects unknown tools with a clear
error. Context selection, prompt caching and non-OpenAI protocols come later.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
import time
import urllib.error
import urllib.request

from .action_parsing import extract_json, normalize_args, strip_fence
from .agent import Action, PolicyBudgetExceeded
from tools.toolsets import (
    DEFAULT_TOOLSET, tool_names, tool_table_for, validate_tool_names,
)


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = ""
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"

# Bump whenever SYSTEM_TEMPLATE or the exposed tool table changes, so runs stay
# attributable. v3: tool table is now derived from the selected toolset.
# v8: persistent run notes, loop warnings and tool-aware observation clipping.
PROMPT_VERSION = "v8"

DEFAULT_WORKSPACE_HINT = (
    "工作区是仓库根目录；先用 list_files 查看 tests/ 或其他目录，"
    "再用 search_text 找测试；测试文件通常名称含 'test'"
    "（例如 test_*.py），但不要假设具体文件名。"
)

# Presentation metadata: the model only emits tool + args (docs/interfaces.md §2.2).
PHASE_BY_TOOL = {
    "map_repository": "认识仓库",
    "find_symbol": "寻找代码",
    "replace_text": "修改代码",
    "list_files": "列出文件",
    "search_text": "搜索文本",
    "search_regex": "搜索文本",
    "read_lines": "阅读行区间",
    "apply_patch": "应用补丁",
    "create_file": "修改代码",
    "edit_lines": "修改代码",
    "revert_file": "修改代码",
    "show_diff": "检查修改",
    "run_visible_tests": "验证",
    "finish": "提交结果",
}

SYSTEM_TEMPLATE = """你是仓库维护 agent。你的整个回复必须是一个 JSON 对象，格式固定：
{{"tool": "<动作名>", "args": {{...}}}}

动作名与参数：
{tools}

示例（{edit_tool}）：
{edit_example}

规则：
1. 一次只写一个动作；需要多步时分成多次回复。
2. tests/ 下的文件不要修改。
3. 所有参数都放在 args 对象里，不要放在顶层。
4. 修改代码后运行可见测试；finish 会冻结补丁。
5. 回复以 {{ 开始、以 }} 结束；JSON 之外不要有任何字符。任务在下一则消息中给出。
"""


def load_env(path=ENV_PATH):
    """Minimal ``.env`` reader: KEY=VALUE lines, no expansion, no override."""
    path = Path(path)
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


class LLMPolicy:
    """Implements the ``next_action(history)`` policy interface."""

    def __init__(self, task, model=DEFAULT_MODEL, api_key="", base_url=DEFAULT_BASE_URL,
                 transport=None, temperature=0.0, max_tokens=4096, timeout=120,
                 max_history=6, observation_limit=1200, notes_limit=4000,
                 read_repeat_warning=5, stale_edit_steps=10,
                 max_parse_attempts=2, max_dsml_retries=2,
                 input_price=0.0, output_price=0.0, log_path=None,
                 json_mode="auto", toolset=DEFAULT_TOOLSET, tools=None,
                 workspace_hint=None, budget_usd=None, max_total_tokens=None):
        self.task = task
        self.model = model
        self.api_key = api_key
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.transport = transport
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_history = max_history
        self.observation_limit = observation_limit
        self.notes_limit = notes_limit
        self.read_repeat_warning = read_repeat_warning
        self.stale_edit_steps = stale_edit_steps
        if not isinstance(max_history, int) or isinstance(max_history, bool) or max_history < 1:
            raise ValueError("max_history must be a positive integer")
        if (not isinstance(observation_limit, int) or isinstance(observation_limit, bool)
                or observation_limit < 1):
            raise ValueError("observation_limit must be a positive integer")
        if not isinstance(notes_limit, int) or isinstance(notes_limit, bool) or notes_limit < 1:
            raise ValueError("notes_limit must be a positive integer")
        if (not isinstance(read_repeat_warning, int) or isinstance(read_repeat_warning, bool)
                or read_repeat_warning < 1):
            raise ValueError("read_repeat_warning must be a positive integer")
        if (not isinstance(stale_edit_steps, int) or isinstance(stale_edit_steps, bool)
                or stale_edit_steps < 1):
            raise ValueError("stale_edit_steps must be a positive integer")
        if (not isinstance(max_parse_attempts, int) or isinstance(max_parse_attempts, bool)
                or max_parse_attempts < 1):
            raise ValueError("max_parse_attempts must be a positive integer")
        if (not isinstance(max_dsml_retries, int) or isinstance(max_dsml_retries, bool)
                or max_dsml_retries < 0):
            raise ValueError("max_dsml_retries must be a non-negative integer")
        self.max_parse_attempts = max_parse_attempts
        self.max_dsml_retries = max_dsml_retries
        self.input_price = input_price
        self.output_price = output_price
        self.log_path = Path(log_path) if log_path else None
        self.json_mode = json_mode if json_mode in {"auto", "on", "off"} else "auto"
        self.json_supported = True
        self.toolset = toolset
        self.tools = validate_tool_names(tools) if tools is not None else tool_names(toolset)
        self.workspace_hint = workspace_hint or DEFAULT_WORKSPACE_HINT
        self.prompt_version = PROMPT_VERSION
        self.calls = 0
        self.last_usage = None
        self.last_extra_actions = 0
        self.max_total_tokens = max_total_tokens
        self.total_tokens = 0
        self.spent_usd = Decimal("0")
        self.budget_usd = None
        if budget_usd is not None:
            budget = Decimal(str(budget_usd))
            if not budget.is_finite() or budget <= 0:
                raise ValueError("budget_usd must be a positive finite amount")
            if max(input_price, output_price) <= 0:
                raise ValueError("budget_usd requires positive token prices")
            self.budget_usd = budget
        if max_total_tokens is not None and (not isinstance(max_total_tokens, int)
                                             or isinstance(max_total_tokens, bool)
                                             or max_total_tokens < 1):
            raise ValueError("max_total_tokens must be a positive integer")

    # --- prompt -----------------------------------------------------------------
    def system_prompt(self):
        names = self.tools
        if "replace_text" in names:
            edit_tool = "replace_text"
            example = ('{"tool": "replace_text", "args": '
                       '{"path": "config.py", "old": "...", "new": "..."}}')
        else:
            edit_tool = "apply_patch"
            example = ('{"tool": "apply_patch", "args": '
                       '{"patch": "diff --git a/x.py b/x.py ..."}}')
        return SYSTEM_TEMPLATE.format(tools=tool_table_for(self.tools),
                                      edit_tool=edit_tool,
                                      edit_example=example)

    @staticmethod
    def _observation_ok(observation):
        value = observation.get("ok") if isinstance(observation, dict) else None
        return "error" not in observation if value is None else bool(value)

    def _clip(self, value):
        text = json.dumps(value, ensure_ascii=False)
        if len(text) <= self.observation_limit:
            return text
        return text[: self.observation_limit] + "…（已截断）"

    def _read_lines_text(self, observation):
        """Keep read_lines metadata and a contiguous shown range.

        A generic JSON cut would hide the later lines and force the model to
        re-read overlapping ranges. This form says exactly where the visible
        text ends and where to continue.
        """
        result = observation.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("lines"), list):
            return self._clip(observation)
        base = {key: observation[key] for key in
                ("ok", "tool", "error", "kind", "fatal", "changed_paths")
                if key in observation}
        compact = {key: result[key] for key in
                   ("path", "start", "end", "total_lines", "truncated")
                   if key in result}
        compact["lines"] = []
        line_budget = max(1, self.observation_limit - 320)

        def truncation_note(last):
            if last is None:
                return "没有任何一行能完整显示；请缩小读取范围。"
            return (f"只显示到 line {last}；若要继续读取，请用 "
                    f"read_lines(start={last + 1}, end=...)。")

        for item in result["lines"]:
            compact["lines"].append(item)
            if len(json.dumps({**base, "result": compact}, ensure_ascii=False)) > line_budget:
                compact["lines"].pop()
                break
        if not compact["lines"] and result["lines"]:
            # Always try to show at least one line; the truncation note is
            # still added below and the final size check may remove it.
            compact["lines"].append(result["lines"][0])
        total = len(result["lines"])
        shown = len(compact["lines"])
        if shown < total:
            last = compact["lines"][-1].get("line") if compact["lines"] else None
            compact["shown_until"] = last
            compact["omitted"] = (f"{last + 1}-{result.get('end')}" if last is not None
                                  else "all")
            compact["note"] = truncation_note(last)
        base["result"] = compact
        text = json.dumps(base, ensure_ascii=False)
        # The note itself must survive: trim lines, not the note.
        while compact["lines"] and len(text) > self.observation_limit:
            compact["lines"].pop()
            last = compact["lines"][-1].get("line") if compact["lines"] else None
            compact["shown_until"] = last
            compact["omitted"] = (f"{last + 1}-{result.get('end')}" if last is not None
                                  else "all")
            compact["note"] = truncation_note(last)
            text = json.dumps(base, ensure_ascii=False)
        return text if len(text) <= self.observation_limit else self._clip(base)

    def _test_text(self, observation):
        """Preserve test status/counts while trimming the huge process output."""
        result = observation.get("result")
        if not isinstance(result, dict):
            return self._clip(observation)
        base = {key: observation[key] for key in
                ("ok", "tool", "error", "kind", "fatal", "changed_paths")
                if key in observation}
        compact = {key: result[key] for key in
                   ("status", "passed", "failed", "errors", "skipped", "collected",
                    "returncode", "timed_out", "truncated")
                   if key in result}
        stdout = result.get("stdout") or result.get("output") or ""
        stderr = result.get("stderr") or ""
        if stdout:
            compact["stdout_tail"] = str(stdout)[-600:]
        if stderr:
            compact["stderr_tail"] = str(stderr)[-300:]
        if result.get("error"):
            compact["error"] = str(result["error"])[:300]
        cases = result.get("cases")
        if isinstance(cases, list) and cases:
            compact["cases"] = cases[:3]
            compact["cases_total"] = len(cases)
        base["result"] = compact
        return self._clip(base)

    def _search_text(self, observation):
        """Keep the first matches and the total; trim the long text fields."""
        result = observation.get("result")
        if not isinstance(result, dict):
            return self._clip(observation)
        base = {key: observation[key] for key in
                ("ok", "tool", "error", "kind", "fatal", "changed_paths")
                if key in observation}
        compact = {key: result[key] for key in
                   ("query", "path", "total", "truncated", "binary_files_skipped")
                   if key in result}
        compact["matches"] = []
        matches = result.get("matches") if isinstance(result.get("matches"), list) else []
        for item in matches:
            compact["matches"].append(item)
            if len(json.dumps({**base, "result": compact}, ensure_ascii=False)) > self.observation_limit:
                compact["matches"].pop()
                break
        if len(compact["matches"]) < len(matches):
            compact["shown"] = len(compact["matches"])
            compact["note"] = "只显示前几笔匹配；请缩小 path 或 query。"
        base["result"] = compact
        text = json.dumps(base, ensure_ascii=False)
        return text if len(text) <= self.observation_limit else self._clip(base)

    def _observation_text(self, observation):
        if not isinstance(observation, dict):
            return self._clip(observation)
        tool = observation.get("tool")
        if tool == "read_lines":
            return self._read_lines_text(observation)
        if tool == "run_visible_tests":
            return self._test_text(observation)
        if tool == "search_text":
            return self._search_text(observation)
        return self._clip(observation)

    @staticmethod
    def _ranges_text(ranges):
        items = sorted(ranges.items(), key=lambda item: (-item[1], item[0]))
        parts = []
        for (start, end), count in items[:4]:
            label = f"{start}–{end}" if end is not None else f"{start}–"
            if count > 1:
                label += f"×{count}"
            parts.append(label)
        if len(items) > 4:
            parts.append(f"+{len(items) - 4} 其他")
        return "，".join(parts) if parts else "—"

    def _notes_text(self, history):
        """Build persistent notes from the entire history, not just the window."""
        if not history:
            return "尚无操作，这是第一步。"
        reads = {}
        edits = {}
        tests = []
        searches = {}
        discovered = set()
        read_or_edited = set()
        last_edit_step = None

        for event in history:
            step = event.get("step")
            action = event.get("action") or {}
            tool = action.get("tool")
            args = action.get("args") or {}
            observation = event.get("observation") or {}
            result = observation.get("result") if isinstance(observation, dict) else None
            result = result if isinstance(result, dict) else {}
            ok = self._observation_ok(observation)

            if tool == "read_lines" and ok:
                path = args.get("path") or result.get("path")
                if path:
                    entry = reads.setdefault(path, {"count": 0, "ranges": {}})
                    entry["count"] += 1
                    start = result.get("start", args.get("start", 1))
                    end = result.get("end", args.get("end"))
                    key = (start, end)
                    entry["ranges"][key] = entry["ranges"].get(key, 0) + 1
                    read_or_edited.add(path)
            elif tool in {"replace_text", "apply_patch"} and ok:
                changed = observation.get("changed_paths") or result.get("changed_paths") or []
                if not changed and args.get("path"):
                    changed = [args["path"]]
                for path in changed:
                    edits.setdefault(path, []).append(step)
                    read_or_edited.add(path)
                    last_edit_step = step
            elif tool == "run_visible_tests" and ok:
                tests.append({
                    "step": step,
                    "targets": list(args.get("paths") or []),
                    "status": result.get("status", observation.get("status", "unknown")),
                    "passed": result.get("passed"),
                    "failed": result.get("failed"),
                })
            elif tool == "search_text" and ok:
                query = args.get("query", "")
                total = result.get("total", len(result.get("matches") or []))
                entry = searches.setdefault(query, {"count": 0, "last_step": step, "total": total})
                entry["count"] += 1
                entry["last_step"] = step
                entry["total"] = total
                for match in result.get("matches") or []:
                    path = match.get("path") if isinstance(match, dict) else None
                    if path and path not in read_or_edited:
                        discovered.add(path)

        history_steps = len(history)
        warnings = []
        if reads:
            path, entry = max(reads.items(), key=lambda item: item[1]["count"])
            if entry["count"] >= self.read_repeat_warning:
                warnings.append(
                    f"重复读取：{path} 已读取 {entry['count']} 次；"
                    "请基于已读内容直接做最小修改或 finish，不要继续重复读取。")
        if last_edit_step is None and history_steps >= self.stale_edit_steps:
            warnings.append(
                f"已连续 {history_steps} 步没有修改任何文件；请优先做最小修改。")
        elif (last_edit_step is not None
              and history_steps - last_edit_step >= self.stale_edit_steps):
            warnings.append(
                f"自 step {last_edit_step} 后已 {history_steps - last_edit_step} 步没有新的修改；"
                "请优先做最小修改或 finish。")

        lines = ["持久摘要（由完整历史生成，不受最近几步窗口限制）："]
        for warning in warnings:
            lines.append("- 警告：" + warning)
        if reads:
            ordered = sorted(reads.items(), key=lambda item: -item[1]["count"])[:6]
            for path, entry in ordered:
                lines.append(f"- 已读 {path} ×{entry['count']}，范围 "
                             f"{self._ranges_text(entry['ranges'])}")
        else:
            lines.append("- 尚未读取任何文件。")
        if edits:
            ordered = sorted(edits.items(), key=lambda item: item[1][-1])[:6]
            for path, steps in ordered:
                lines.append(f"- 已修改 {path}（step {', '.join(map(str, steps))}）")
        else:
            lines.append("- 尚未修改任何文件。")
        if tests:
            for item in tests[-5:]:
                targets = item["targets"]
                target_text = ", ".join(targets[:2]) + (" 等" if len(targets) > 2 else "")
                lines.append(f"- step {item['step']} 测试 {target_text or '—'} -> "
                             f"{item['status']}（passed={item['passed']}, failed={item['failed']}）")
        if searches:
            for query, entry in sorted(searches.items(), key=lambda item: item[1]["last_step"])[-5:]:
                lines.append(f"- step {entry['last_step']} 搜索 {query!r} -> "
                             f"{entry['total']} 笔匹配（共 {entry['count']} 次）")
        unread = sorted(discovered - read_or_edited - set(edits))
        if unread:
            lines.append("- 搜索发现但尚未读取/修改：" + "，".join(unread[:8]))
        text = "\n".join(lines)
        if len(text) > self.notes_limit:
            text = text[: self.notes_limit] + "\n…（摘要已截断）"
        return text

    def _history_text(self, history):
        if not history:
            return "尚无操作，这是第一步。"
        lines = []
        for event in history[-self.max_history:]:
            action = event.get("action", {})
            args = json.dumps(action.get("args", {}), ensure_ascii=False)
            lines.append(f"[{event.get('step')}] {action.get('tool')}({args})")
            lines.append(f"    结果: {self._observation_text(event.get('observation', {}))}")
        return "\n".join(lines)

    def _messages(self, history):
        if history:
            recent_count = min(len(history), self.max_history)
            context = (f"持久摘要：\n{self._notes_text(history)}\n\n"
                       f"最近 {recent_count} 步：\n{self._history_text(history)}")
        else:
            context = "尚无操作，这是第一步。"
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": (
                f"任务：{self.task}\n\n"
                f"{self.workspace_hint}\n\n"
                f"到目前为止：\n{context}"
            )},
        ]
        if self.last_extra_actions:
            messages.append({"role": "user", "content": (
                f"注意：上一则回复里的动作不止一个（共 {self.last_extra_actions + 1} 个），"
                "我只执行了第一个。请一次只输出一个 JSON 动作。"
            )})
        return messages

    # --- HTTP -------------------------------------------------------------------
    def _post(self, messages):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.json_mode != "off" and self.json_supported:
            payload["response_format"] = {"type": "json_object"}
        if self.transport is not None:
            return self.transport(payload)
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            # Some OpenAI-compatible endpoints reject response_format; drop it once.
            if (exc.code == 400 and self.json_mode == "auto" and self.json_supported
                    and "response_format" in detail.lower()):
                self.json_supported = False
                return self._post(messages)
            raise RuntimeError(f"LLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM 连接失败：{exc.reason}") from exc

    # --- parsing ----------------------------------------------------------------
    @staticmethod
    def _content(reply):
        choices = reply.get("choices") or [{}]
        message = choices[0].get("message") or {}
        return message.get("content") or ""

    _strip_fence = staticmethod(strip_fence)
    _extract_json = staticmethod(extract_json)
    _normalize_args = staticmethod(normalize_args)

    def _parse(self, reply):
        data, extra = self._extract_json(self._content(reply))
        self.last_extra_actions = extra
        tool = data["tool"]
        if tool not in self.tools:
            raise ValueError(f"tool {tool!r} is not enabled for this run")
        return Action(tool, self._normalize_args(data), PHASE_BY_TOOL.get(tool, ""), "")

    def _cost(self, prompt_tokens, completion_tokens):
        return round(
            prompt_tokens * self.input_price / 1_000_000
            + completion_tokens * self.output_price / 1_000_000,
            6,
        )

    def _usage(self, prompt_tokens, completion_tokens, attempts, last_error,
               parse_attempts=0, dsml_retries=0):
        return {
            "model": self.model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": self._cost(prompt_tokens, completion_tokens),
            "attempts": attempts,
            "parse_attempts": parse_attempts,
            "dsml_retries": dsml_retries,
            "last_error": last_error,
            "extra_actions": self.last_extra_actions,
        }

    def _record(self, step, attempt, messages, reply, error, elapsed_ms):
        """Append one line per API call (success or failure) when logging is on."""
        if self.log_path is None:
            return
        usage = (reply or {}).get("usage") or {}
        record = {
            "step": step,
            "attempt": attempt,
            "model": self.model,
            "messages": messages,
            "raw_content": self._content(reply) if reply is not None else None,
            "usage": {
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            },
            "error": error,
            "elapsed_ms": elapsed_ms,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _reserve(self, messages):
        if self.budget_usd is None and self.max_total_tokens is None:
            return
        prompt_tokens = max(1, len(json.dumps(messages, ensure_ascii=False)) // 4)
        reserve_tokens = prompt_tokens + self.max_tokens
        if (self.max_total_tokens is not None
                and self.total_tokens + reserve_tokens > self.max_total_tokens):
            raise PolicyBudgetExceeded("Model token budget would be exceeded")
        if self.budget_usd is not None:
            worst_price = max(self.input_price, self.output_price)
            reserve_cost = (Decimal(reserve_tokens) * Decimal(str(worst_price))
                            / Decimal(1_000_000))
            if self.spent_usd + reserve_cost > self.budget_usd:
                raise PolicyBudgetExceeded("Model cost budget would be exceeded")

    def next_action(self, history):
        step = len(history) + 1
        messages = self._messages(history)
        prompt_tokens = completion_tokens = 0
        api_attempts = 0
        parse_attempts = 0
        dsml_retries = 0
        last_error = None
        last_kind = None
        max_api_calls = self.max_parse_attempts + self.max_dsml_retries
        retry_parse = (
            "上一条回复不是要求的 JSON。只输出一个 JSON 对象，"
            "不要输出任何其他字符：\n"
            '{"tool": "<动作名>", "args": {...}}'
        )
        retry_dsml = (
            "上一条包含 DSML/XML 工具标记，不是合法 JSON。"
            "不要使用 DSML、XML 或 tool_calls；只输出一个 JSON 对象，"
            "不要输出任何其他字符：\n"
            '{"tool": "<动作名>", "args": {...}}'
        )
        while (parse_attempts < self.max_parse_attempts
               and api_attempts < max_api_calls):
            if last_kind == "dsml":
                instruction = retry_dsml
            elif last_kind == "parse":
                instruction = retry_parse
            else:
                instruction = None
            batch = messages if instruction is None else messages + [{
                "role": "user", "content": instruction,
            }]
            self._reserve(batch)
            started = time.perf_counter()
            try:
                reply = self._post(batch)
            except Exception as exc:
                self._record(step, api_attempts + 1, batch, None,
                             f"{type(exc).__name__}: {exc}",
                             round((time.perf_counter() - started) * 1000))
                raise
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            self.calls += 1
            api_attempts += 1
            usage = reply.get("usage") or {}
            prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            self.total_tokens += int(usage.get("prompt_tokens", 0) or 0) + int(usage.get("completion_tokens", 0) or 0)
            self.spent_usd += Decimal(str(self._cost(int(usage.get("prompt_tokens", 0) or 0),
                                                     int(usage.get("completion_tokens", 0) or 0))))
            if self.max_total_tokens is not None and self.total_tokens > self.max_total_tokens:
                raise PolicyBudgetExceeded("Model token budget exceeded")
            if self.budget_usd is not None and self.spent_usd > self.budget_usd:
                raise PolicyBudgetExceeded("Model cost budget exceeded")
            try:
                action = self._parse(reply)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                content = self._content(reply) or ""
                if "DSML" in content and dsml_retries < self.max_dsml_retries:
                    dsml_retries += 1
                    last_kind = "dsml"
                    self._record(step, api_attempts, batch, reply, last_error, elapsed_ms)
                    continue
                parse_attempts += 1
                last_kind = "parse"
                self._record(step, api_attempts, batch, reply, last_error, elapsed_ms)
                continue
            self._record(step, api_attempts, batch, reply, None, elapsed_ms)
            self.last_usage = self._usage(prompt_tokens, completion_tokens,
                                          api_attempts, last_error,
                                          parse_attempts, dsml_retries)
            return action
        # Keep the failed attempt visible even though no event will record it.
        self.last_usage = self._usage(prompt_tokens, completion_tokens,
                                      api_attempts, last_error,
                                      parse_attempts, dsml_retries)
        raise RuntimeError(
            f"模型无法解析输出（parse_attempts={parse_attempts}, "
            f"dsml_retries={dsml_retries}, api_attempts={api_attempts}）：{last_error}")


def _first_env(*names, default=""):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def policy_from_env(task, model=None, env_path=ENV_PATH, transport=None,
                    toolset=None, tools=None, workspace_hint=None, **kwargs):
    """Build an LLMPolicy for any OpenAI-compatible endpoint.

    Environment (``.env`` or process):

    - ``LLM_API_KEY`` / ``LLM_MODEL`` / ``LLM_BASE_URL``
    - ``LLM_JSON_MODE`` = ``auto`` (default) | ``on`` | ``off``
    - ``LLM_PRICE_INPUT`` / ``LLM_PRICE_OUTPUT`` (USD per 1M tokens)

    Legacy ``DEEPSEEK_*`` names still work as fallbacks.
    """
    load_env(env_path)
    api_key = _first_env("LLM_API_KEY", "DEEPSEEK_API_KEY").strip()
    if not api_key:
        raise RuntimeError(
            "缺少 LLM_API_KEY：请写入 .env 或设置为环境变量后再运行。"
        )
    resolved_model = model or _first_env("LLM_MODEL", "DEEPSEEK_MODEL")
    if not resolved_model:
        raise RuntimeError("缺少 LLM_MODEL：请在 .env 设置模型名或使用 --model。")
    kwargs.setdefault(
        "max_parse_attempts",
        int(_first_env("LLM_MAX_PARSE_ATTEMPTS", default="2")))
    kwargs.setdefault(
        "max_dsml_retries",
        int(_first_env("LLM_MAX_DSML_RETRIES", default="2")))
    kwargs.setdefault(
        "max_history",
        int(_first_env("LLM_MAX_HISTORY", default="6")))
    kwargs.setdefault(
        "observation_limit",
        int(_first_env("LLM_OBSERVATION_LIMIT", default="1200")))
    kwargs.setdefault(
        "notes_limit",
        int(_first_env("LLM_NOTES_LIMIT", default="4000")))
    kwargs.setdefault(
        "read_repeat_warning",
        int(_first_env("LLM_READ_REPEAT_WARNING", default="5")))
    kwargs.setdefault(
        "stale_edit_steps",
        int(_first_env("LLM_STALE_EDIT_STEPS", default="10")))
    return LLMPolicy(
        task=task,
        model=resolved_model,
        api_key=api_key,
        base_url=_first_env("LLM_BASE_URL", "DEEPSEEK_BASE_URL", default=DEFAULT_BASE_URL),
        transport=transport,
        max_tokens=int(_first_env("LLM_MAX_TOKENS", default="4096")),
        input_price=float(_first_env("LLM_PRICE_INPUT", "DEEPSEEK_PRICE_INPUT", default="0")),
        output_price=float(_first_env("LLM_PRICE_OUTPUT", "DEEPSEEK_PRICE_OUTPUT", default="0")),
        json_mode=_first_env("LLM_JSON_MODE", default="auto").strip().lower(),
        toolset=toolset or _first_env("LLM_TOOLSET", default=DEFAULT_TOOLSET),
        tools=tools,
        workspace_hint=workspace_hint,
        **kwargs,
    )
