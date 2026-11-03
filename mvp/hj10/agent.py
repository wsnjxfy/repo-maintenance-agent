"""The controller does not know the solution or have access to hidden tests."""

from dataclasses import dataclass, field
import json
import time
from typing import Protocol


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

    def run(self, policy: Policy, trajectory_path):
        history = []
        status, reason = "budget_exhausted", "已达到工具调用步数上限。"
        tool_errors = 0
        with trajectory_path.open("w", encoding="utf-8") as log:
            for step in range(1, self.max_steps + 1):
                try:
                    action = policy.next_action(history)
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
                    observation = {"error": f"{type(exc).__name__}: {exc}"}
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
                detail = observation.get("status", "error" if "error" in observation else "ok")
                print(f"[{step:02d}] {action.phase}: {action.tool} -> {detail}", flush=True)
                if "error" in observation and action.tool != "run_tests":
                    tool_errors += 1
                    if tool_errors >= self.max_tool_errors:
                        status = "policy_failed"
                        reason = (f"连续 {tool_errors} 次工具错误，最后一条："
                                  f"{observation['error']}")
                        break
                    # Recoverable: the error stays in history so the policy can correct it.
                    continue
                tool_errors = 0
                if observation.get("status") in {"environment_error", "test_load_error", "execution_timeout"}:
                    status, reason = "incomplete", "测试未能完成，已停止；需根据日志判断环境或代码原因。"
                    break
                if action.tool == "finish":
                    status = "completed" if observation["accepted"] else "incomplete"
                    reason = observation["reason"]
                    break
        return history, status, reason
