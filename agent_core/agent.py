"""The controller does not know the solution or have access to hidden tests."""

from dataclasses import dataclass, field
import json
import sys
import time
from typing import Protocol


class PolicyBudgetExceeded(RuntimeError):
    """Raised by a policy when its model/token budget would be exceeded."""


def _emit(message):
    """Print progress without crashing on non-UTF-8 consoles (e.g. cp1252 CI)."""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        safe = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, flush=True)


@dataclass
class Action:
    tool: str
    args: dict = field(default_factory=dict)
    phase: str = ""
    intent: str = ""


class Policy(Protocol):
    def next_action(self, history: list[dict]) -> Action | None:
        """A future model adapter can implement this exact interface."""
        ...


class Agent:
    def __init__(self, tools, max_steps=24, max_tool_errors=3):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        if max_tool_errors < 1:
            raise ValueError("max_tool_errors must be positive")
        self.tools = tools
        self.max_steps = max_steps
        self.max_tool_errors = max_tool_errors

    @staticmethod
    def _ok(observation):
        """v2 uses ``ok``; legacy backends signal failure with an ``error`` key."""
        value = observation.get("ok")
        return "error" not in observation if value is None else bool(value)

    @staticmethod
    def _result(observation):
        result = observation.get("result")
        return result if isinstance(result, dict) else {}

    @classmethod
    def _fatal(cls, observation):
        if observation.get("fatal"):
            return True
        status = cls._result(observation).get("status", observation.get("status"))
        return status in {"environment_error", "test_load_error", "execution_timeout"}

    def run(self, policy: Policy, trajectory_path):
        history = []
        status, reason = "budget_exhausted", "已达到工具调用步数上限。"
        tool_errors = 0
        with trajectory_path.open("w", encoding="utf-8") as log:
            for step in range(1, self.max_steps + 1):
                try:
                    action = policy.next_action(history)
                except PolicyBudgetExceeded as exc:
                    status, reason = "budget_exhausted", str(exc)
                    break
                except Exception as exc:
                    status, reason = "policy_failed", f"决策器错误：{type(exc).__name__}: {exc}"
                    break
                if action is None:
                    status, reason = "incomplete", "决策器结束，但尚未提交有效完成请求。"
                    break
                started = time.perf_counter()
                try:
                    observation = self.tools.dispatch(action.tool, action.args)
                except Exception as exc:
                    observation = {"ok": False, "tool": action.tool,
                                   "error": f"{type(exc).__name__}: {exc}", "kind": "tool"}
                event = {"step": step, "phase": action.phase, "intent": action.intent,
                         "action": {"tool": action.tool, "args": action.args},
                         "observation": observation,
                         "elapsed_ms": round((time.perf_counter() - started) * 1000)}
                usage = getattr(policy, "last_usage", None)
                if usage:
                    event["llm"] = usage
                history.append(event)
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
                log.flush()
                ok = self._ok(observation)
                detail = observation.get("status") or ("ok" if ok else "error")
                _emit(f"[{step:02d}] {action.phase}: {action.tool} -> {detail}")
                if not ok:
                    kind = observation.get("kind", "tool")
                    if kind in {"budget", "timeout"}:
                        status = "budget_exhausted"
                        reason = observation.get("error") or "动作预算或墙钟耗尽。"
                        break
                    tool_errors += 1
                    if tool_errors >= self.max_tool_errors:
                        status = "policy_failed"
                        reason = (f"连续 {tool_errors} 次工具错误，最后一条："
                                  f"{observation.get('error', '')}")
                        break
                    # Recoverable: the error stays in history so the policy can correct it.
                    continue
                tool_errors = 0
                if self._fatal(observation):
                    status, reason = "incomplete", "测试未能完成，已停止；需根据日志判断环境或代码原因。"
                    break
                if action.tool == "finish":
                    result = self._result(observation)
                    accepted = observation.get("accepted", result.get("accepted"))
                    reason = (observation.get("reason") or result.get("reason")
                              or result.get("stop_reason") or "已提交完成请求。")
                    status = "incomplete" if accepted is False else "completed"
                    break
        return history, status, reason
