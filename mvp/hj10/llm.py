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
from pathlib import Path
import time
import urllib.error
import urllib.request

from .agent import Action
from .tools import TOOL_SPECS, tool_table


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = ""
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# Bump whenever SYSTEM_TEMPLATE changes, so runs stay attributable.
PROMPT_VERSION = "v2"

# Presentation metadata: the model only emits tool + args (docs/interfaces.md §2.2).
PHASE_BY_TOOL = {
    "map_repository": "认识仓库",
    "find_symbol": "寻找代码",
    "read_file": "阅读上下文",
    "plan": "制定计划",
    "replace_text": "修改代码",
    "run_tests": "验证",
    "finish": "提交结果",
}

SYSTEM_TEMPLATE = """你是一个仓库维护 agent，任务是在给定工作区里修好代码，让可见测试通过。

可用工具（每一步只调用一个）：
{tools}

输出要求：只输出一个 JSON 对象，不要解释、不要 Markdown 代码块：
{{"tool": "<工具名>", "args": {{...}}}}

正确示例（plan）：
{{"tool": "plan", "args": {{"steps": ["第一步", "第二步"]}}}}

工作规则：
1. tests/ 下的文件只读，不要尝试修改它们。
2. 修改代码后必须重新调用 run_tests；只有最新一次测试全部通过，finish 才会被接受。
3. 不要重复读同一个文件；先用 map_repository 或 find_symbol 缩小范围。
4. 所有参数都放在 args 对象里。例如 {{"tool": "plan", "args": {{"steps": ["第一步"]}}}}；
   不要写成 {{"tool": "plan", "steps": [...]}}，也不要把工具签名当成顶层字段。
5. 一次只输出一个 JSON 对象。即使需要读多个文件，也要分成多个步骤，不要在同一则回复里给多个动作。
6. 推理保持简短；JSON 之外不要输出任何内容。
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
    """Implements the same ``next_action(history)`` interface as ScriptedPolicy."""

    def __init__(self, task, model=DEFAULT_MODEL, api_key="", base_url=DEFAULT_BASE_URL,
                 transport=None, temperature=0.0, max_tokens=4096, timeout=120,
                 max_history=6, observation_limit=1200,
                 input_price=0.0, output_price=0.0, log_path=None,
                 json_mode="auto"):
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
        self.input_price = input_price
        self.output_price = output_price
        self.log_path = Path(log_path) if log_path else None
        self.json_mode = json_mode if json_mode in {"auto", "on", "off"} else "auto"
        self.json_supported = True
        self.prompt_version = PROMPT_VERSION
        self.calls = 0
        self.last_usage = None
        self.last_extra_actions = 0

    # --- prompt -----------------------------------------------------------------
    def system_prompt(self):
        return SYSTEM_TEMPLATE.format(tools=tool_table())

    def _clip(self, value):
        text = json.dumps(value, ensure_ascii=False)
        if len(text) <= self.observation_limit:
            return text
        return text[: self.observation_limit] + "…（已截断）"

    def _history_text(self, history):
        if not history:
            return "尚无操作，这是第一步。"
        lines = []
        for event in history[-self.max_history:]:
            action = event.get("action", {})
            args = json.dumps(action.get("args", {}), ensure_ascii=False)
            lines.append(f"[{event.get('step')}] {action.get('tool')}({args})")
            lines.append(f"    结果: {self._clip(event.get('observation', {}))}")
        return "\n".join(lines)

    def _messages(self, history):
        messages = [
            {"role": "system", "content": self.system_prompt()},
            {"role": "user", "content": (
                f"任务：{self.task}\n\n"
                "当前工作区：仓库根目录，包含 Python 源码与 tests/test_visible.py。\n\n"
                f"到目前为止：\n{self._history_text(history)}"
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

    @staticmethod
    def _strip_fence(text):
        stripped = (text or "").strip()
        if stripped.startswith("```"):
            _, _, body = stripped.partition("\n")
            stripped = body if body else stripped[3:]
            if stripped.rstrip().endswith("```"):
                stripped = stripped.rstrip()[:-3]
        return stripped.strip()

    @classmethod
    def _extract_json(cls, text):
        """Accept bare JSON, fenced JSON, JSON in prose, and multiple objects.

        Returns ``(first_value, extra_count)``; ``extra_count`` is the number of
        additional JSON values the model emitted in the same reply. The first
        value wins, so one protocol violation cannot abort the whole run.
        """
        stripped = cls._strip_fence(text)
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            value = None
        if value is not None:
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, dict)]
                if not items:
                    return value, 0
                return items[0], max(len(items) - 1, 0)
            return value, 0
        decoder = json.JSONDecoder()
        first, extra, index = None, 0, 0
        while index < len(stripped):
            start = stripped.find("{", index)
            if start == -1:
                break
            try:
                value, end = decoder.raw_decode(stripped, start)
            except json.JSONDecodeError:
                index = start + 1
                continue
            if first is None:
                first = value
            else:
                extra += 1
            index = end
        if first is None:
            raise json.JSONDecodeError("No JSON object found", stripped, 0)
        return first, extra

    @staticmethod
    def _normalize_args(data):
        """Accept ``args`` nesting, an ``arguments`` JSON string, or top-level keys.

        Models that follow the function-calling convention often put the tool
        parameters at the top level (``{"tool": "plan", "steps": [...]}``).
        Accepting both shapes avoids a fatal-looking tool error for what is only
        a formatting preference.
        """
        args = data.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = None
        if not isinstance(args, dict) or not args:
            arguments = data.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = None
            if isinstance(arguments, dict) and arguments:
                args = arguments
        if not isinstance(args, dict) or not args:
            loose = {key: value for key, value in data.items()
                     if key not in {"tool", "args", "arguments"}}
            if loose:
                args = loose
        return args if isinstance(args, dict) else {}

    def _parse(self, reply):
        data, extra = self._extract_json(self._content(reply))
        self.last_extra_actions = extra
        tool = data["tool"]
        if tool not in TOOL_SPECS:
            raise ValueError(f"unknown tool {tool!r}")
        return Action(tool, self._normalize_args(data), PHASE_BY_TOOL.get(tool, ""), "")

    def _cost(self, prompt_tokens, completion_tokens):
        return round(
            prompt_tokens * self.input_price / 1_000_000
            + completion_tokens * self.output_price / 1_000_000,
            6,
        )

    def _usage(self, prompt_tokens, completion_tokens, attempts, last_error):
        return {
            "model": self.model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_usd": self._cost(prompt_tokens, completion_tokens),
            "attempts": attempts,
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

    def next_action(self, history):
        step = len(history) + 1
        messages = self._messages(history)
        prompt_tokens = completion_tokens = 0
        attempts = 0
        last_error = None
        for attempt in range(2):
            batch = messages if attempt == 0 else messages + [{
                "role": "user",
                "content": f"上一次输出无法使用：{last_error}\n请重新只输出一个合法 JSON 对象。",
            }]
            started = time.perf_counter()
            try:
                reply = self._post(batch)
            except Exception as exc:
                self._record(step, attempt + 1, batch, None,
                             f"{type(exc).__name__}: {exc}",
                             round((time.perf_counter() - started) * 1000))
                raise
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            self.calls += 1
            attempts += 1
            usage = reply.get("usage") or {}
            prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            try:
                action = self._parse(reply)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._record(step, attempts, batch, reply, last_error, elapsed_ms)
                continue
            self._record(step, attempts, batch, reply, None, elapsed_ms)
            self.last_usage = self._usage(prompt_tokens, completion_tokens,
                                          attempts, last_error)
            return action
        # Keep the failed attempt visible even though no event will record it.
        self.last_usage = self._usage(prompt_tokens, completion_tokens,
                                      attempts, last_error)
        raise RuntimeError(f"模型连续两次返回无法解析的输出：{last_error}")


def _first_env(*names, default=""):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def policy_from_env(task, model=None, env_path=ENV_PATH, transport=None, **kwargs):
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
        **kwargs,
    )
