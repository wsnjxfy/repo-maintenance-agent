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
    def __init__(self, tools, max_steps=24):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self.tools = tools
        self.max_steps = max_steps

    def run(self, policy: Policy, trajectory_path):
        history = []
        status, reason = "budget_exhausted", "已达到工具调用步数上限。"
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
                history.append(event)
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
                log.flush()
                detail = observation.get("status", "error" if "error" in observation else "ok")
                print(f"[{step:02d}] {action.phase}: {action.tool} -> {detail}", flush=True)
                if "error" in observation and action.tool != "run_tests":
                    status, reason = "policy_failed", observation["error"]
                    break
                if observation.get("status") in {"environment_error", "test_load_error", "execution_timeout"}:
                    status, reason = "incomplete", "测试未能完成，已停止；需根据日志判断环境或代码原因。"
                    break
                if action.tool == "finish":
                    status = "completed" if observation["accepted"] else "incomplete"
                    reason = observation["reason"]
                    break
        return history, status, reason
