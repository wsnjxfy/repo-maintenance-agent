"""Run one Sphinx 7462 baseline with a scripted model or DeepSeek Flash."""

import argparse
from dataclasses import dataclass
from decimal import Decimal
import json
import math
from pathlib import Path
import subprocess
import time
from typing import Optional

from experiments.sphinx_7462.edit_phases import EditPhases
from experiments.sphinx_7462.edit_transition import EditTransition
from experiments.sphinx_7462.evaluate import evaluate
from experiments.sphinx_7462.prepare import SOURCE_SHA256, digest, prepare, protocol
from tooling.backends.repo import ToolRunner, _write_json, activate_run
from hj10.action_parsing import extract_json, normalize_args


TOOL_ARGUMENTS = {
    "list_files": "path (default '.'), limit (optional)",
    "search_text": "literal query, path (default '.'), limit and case_sensitive (optional)",
    "read_lines": "path, start and end line numbers (optional)",
    "apply_patch": "patch (unified diff with a/ and b/ paths)",
    "run_visible_tests": "paths (list of original pytest targets), timeout_seconds (optional)",
    "finish": "reason (nonempty string)",
}
REPLACE_TEXT_ARGUMENTS = "path (existing relative file), old (exact text occurring once), new (replacement text)"
INSTRUCTIONS = (
    "Work on the original issue using the provided workspace tools. "
    'Return exactly one JSON object of the form {"tool": "name", "args": {...}}. '
    "Original tests and configuration are read-only. Use visible tests for feedback. "
    "Call finish to freeze the current patch. Independent evaluation happens after stopping."
)
HISTORY_WINDOW = 6
OBSERVATION_LIMIT = 6000


@dataclass(frozen=True)
class ModelReply:
    text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    api: Optional[dict] = None


class ScriptedModel:
    """Return local responses in order. Strings may represent malformed responses."""

    def __init__(self, replies):
        if not isinstance(replies, list) or not all(isinstance(reply, (str, dict)) for reply in replies):
            raise ValueError("Mock replies must be a JSON array of action objects or strings")
        self.replies = iter([
            reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
            for reply in replies
        ])

    def __call__(self, request, *, timeout_seconds):
        return ModelReply(next(self.replies))


class ApiBudgetReached(RuntimeError):
    pass


class DeepSeekModel:
    """Adapt Charles's single Chat Completions request to the baseline protocol."""

    # Peak USD per million tokens, using integer billionths of a dollar per token.
    INPUT_MISS = 300
    INPUT_HIT = 6
    OUTPUT = 1200
    PROVIDER_CONTEXT = 1_048_576
    INPUT_ESTIMATE_LIMIT = 12_000

    def __init__(self, policy, budget_usd):
        if (policy.model != "deepseek-flash"
                or policy.endpoint != "https://api.deepseek.com/chat/completions"):
            raise ValueError("Paid mode requires the official deepseek-flash endpoint")
        if policy.json_mode != "on" or not 1 <= policy.max_tokens <= 4096:
            raise ValueError("Paid mode requires JSON mode on and at most 4096 output tokens")
        budget = Decimal(str(budget_usd))
        if not budget.is_finite() or budget <= 0:
            raise ValueError("The API budget must be a positive dollar amount")
        self.policy = policy
        self.budget = int(budget * 1_000_000_000)
        self.http_requests = 0
        self.peak_cost = 0
        self.peak_uncached_cost = 0
        self.usage_missing = False

    def __call__(self, request, *, timeout_seconds):
        # Reserve the documented context limit so a missing usage response or a
        # tokenizer mismatch cannot make us start a request beyond the budget.
        reserve = self.PROVIDER_CONTEXT * self.INPUT_MISS + self.policy.max_tokens * self.OUTPUT
        if self.usage_missing:
            raise ApiBudgetReached("Prior request has no verified usage")
        if self.peak_uncached_cost + reserve > self.budget:
            raise ApiBudgetReached("Insufficient budget to reserve another request")

        system = {"role": "system", "content": request["instructions"]}
        user = {key: request[key] for key in ("task", "tools", "history")}
        if "edit_transition" in request:
            user["edit_transition"] = request["edit_transition"]
        if "phase" in request:
            user["phase"] = request["phase"]
        while True:
            messages = [system, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]
            input_estimate = len(json.dumps(messages, ensure_ascii=False).encode("utf-8")) + 512
            if input_estimate <= self.INPUT_ESTIMATE_LIMIT:
                break
            if not user["history"]:
                raise ApiBudgetReached("The initial prompt exceeds the request input allowance")
            user["history"] = user["history"][1:]

        self.policy.timeout = min(90, timeout_seconds)
        self.http_requests += 1
        self.usage_missing = True
        try:
            reply = self.policy._post(messages)
        except Exception as exc:
            message = str(exc).replace(self.policy.api_key, "[redacted]")
            if isinstance(exc, TimeoutError):
                raise TimeoutError(message) from None
            raise RuntimeError(message) from None
        usage = reply.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        cache_hit = usage.get("prompt_cache_hit_tokens")
        cost = None
        if all(type(value) is int and value >= 0 for value in (prompt_tokens, completion_tokens)):
            self.peak_uncached_cost += prompt_tokens * self.INPUT_MISS + completion_tokens * self.OUTPUT
            priced_hit = cache_hit if type(cache_hit) is int and 0 <= cache_hit <= prompt_tokens else 0
            cost = priced_hit * self.INPUT_HIT + (prompt_tokens - priced_hit) * self.INPUT_MISS
            cost += completion_tokens * self.OUTPUT
            self.peak_cost += cost
            self.usage_missing = False
        choice = (reply.get("choices") or [{}])[0]
        return ModelReply(self.policy._content(reply), prompt_tokens, completion_tokens, {
            "request_number": self.http_requests, "response_id": reply.get("id"),
            "model": reply.get("model"), "system_fingerprint": reply.get("system_fingerprint"),
            "finish_reason": choice.get("finish_reason"), "history_items_sent": len(user["history"]),
            "input_estimate": input_estimate, "http_timeout_seconds": self.policy.timeout,
            "cache_hit_tokens": cache_hit, "cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "peak_cost_estimate_usd": cost / 1_000_000_000 if cost is not None else None,
            **({"edit_transition_sent": True} if "edit_transition" in user else {}),
        })

    def summary(self):
        return {
            "model": self.policy.model, "endpoint": self.policy.endpoint,
            "json_mode": self.policy.json_mode, "thinking": "provider default",
            "max_output_tokens": self.policy.max_tokens, "http_requests": self.http_requests,
            "budget_usd": self.budget / 1_000_000_000, "usage_missing": self.usage_missing,
            "peak_cost_estimate_usd": None if self.usage_missing else self.peak_cost / 1_000_000_000,
        }


def _error(exc):
    return f"{type(exc).__name__}: {exc}"


def _clip(text):
    if len(text) <= OBSERVATION_LIMIT:
        return text
    marker = "\n[observation truncated]\n"
    half = (OBSERVATION_LIMIT - len(marker)) // 2
    return text[:half] + marker + text[-half:]


def _observation(response, run_dir):
    if response["status"] == "error":
        payload = {"error": response["error"]}
    else:
        payload = response["result"]
        if response["action"] == "run_visible_tests":
            fields = ("status", "passed", "failed", "errors", "skipped", "timed_out",
                      "elapsed_seconds", "output", "stderr_output")
            payload = {key: payload[key] for key in fields}
        elif response["action"] in {"apply_patch", "replace_text"}:
            payload = {"changed_paths": payload["changed_paths"]}
        elif response["action"] == "search_text":
            payload = {key: value for key, value in payload.items() if not key.startswith("full_output")}
    text = json.dumps(payload, ensure_ascii=False)
    for spelling in (str(run_dir), run_dir.as_posix()):
        text = text.replace(json.dumps(spelling, ensure_ascii=False)[1:-1], "[run directory]")
    return _clip(text)


def _validate_action(action, tools):
    if not isinstance(action, dict) or set(action) != {"tool", "args"}:
        raise ValueError("Expected exactly one object with tool and args fields")
    if not isinstance(action["tool"], str) or action["tool"] not in tools:
        raise ValueError("Unknown workspace tool: " + repr(action["tool"]))
    if not isinstance(action["args"], dict):
        raise ValueError("Tool arguments must be a JSON object")
    # Tool inputs must also be serializable to the UTF-8 audit log.
    json.dumps(action, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return action


def _parse(text, tools=TOOL_ARGUMENTS):
    return _validate_action(json.loads(text), tools)


def _parse_with_fallback(text, tools=TOOL_ARGUMENTS):
    try:
        return _parse(text, tools), None
    except (ValueError, TypeError, RecursionError):
        if not isinstance(text, str):
            raise
        data, extra_objects = extract_json(text)
        if not isinstance(data, dict) or "tool" not in data:
            raise ValueError("Expected an object containing a workspace tool")
        # Preserve rejection of explicit, malformed parameters instead of
        # treating them as a call with no parameters.
        for name in ("args", "arguments"):
            if name in data:
                if isinstance(data[name], str):
                    data[name] = json.loads(data[name])
                if not isinstance(data[name], dict):
                    raise ValueError(f"{name} must be a JSON object")
        action = {"tool": data["tool"], "args": normalize_args(data)}
        return _validate_action(action, tools), extra_objects


def _model_call(model, request, number, deadline):
    started = time.monotonic()
    remaining = deadline - started
    if remaining <= 0:
        return None, None, "wall_time_budget"
    call = {"call": number, "request": request, "response": None, "tool_event_id": None,
            "timeout_seconds": remaining, "input_tokens": None, "output_tokens": None}
    action = None
    stop = None
    try:
        reply = model(request, timeout_seconds=remaining)
    except StopIteration:
        call["status"] = stop = "model_exhausted"
    except ApiBudgetReached as exc:
        call.update(status="api_budget", error=_error(exc))
        stop = "api_budget"
    except TimeoutError as exc:
        call.update(status="model_timeout", error=_error(exc))
        stop = "model_timeout"
    except Exception as exc:
        call.update(status="model_error", error=_error(exc))
        stop = "model_error"
    else:
        if isinstance(reply, ModelReply):
            for name in ("input_tokens", "output_tokens"):
                value = getattr(reply, name)
                if type(value) is int and value >= 0:
                    call[name] = value
            if isinstance(reply.text, str):
                call["response"] = reply.text
            if reply.api is not None:
                call["api"] = reply.api
        if time.monotonic() >= deadline:
            call["status"] = "late_response"
            stop = "wall_time_budget"
        else:
            try:
                action, extra_objects = _parse_with_fallback(call["response"], request.get("tools", TOOL_ARGUMENTS))
                call.update(status="ok", parser="strict" if extra_objects is None else "fallback")
                if extra_objects is not None:
                    call["extra_objects"] = extra_objects
            except (ValueError, TypeError, RecursionError) as exc:
                call.update(status="invalid_response", error=_error(exc))
    call["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return call, action, stop


def _solve(runner, task, model, max_actions, max_model_calls, wall_time_seconds, summary, *,
           tools=TOOL_ARGUMENTS, transition=None, phases=None):
    log_path = runner.controller / "model-calls.jsonl"
    started = time.monotonic()
    deadline = started + wall_time_seconds
    history = []
    usage = []
    frozen = None
    stopped_at = None
    pending_transition = False
    with log_path.open("x", encoding="utf-8") as log:
        for number in range(1, max_model_calls + 1):
            if time.monotonic() >= deadline:
                summary["stop_reason"] = "wall_time_budget"
                break
            if summary["workspace_actions"] >= max_actions:
                summary["stop_reason"] = "action_budget"
                break
            request = {"task": task, "instructions": INSTRUCTIONS, "tools": tools,
                       "history": history[-HISTORY_WINDOW:]}
            if phases is not None:
                phase, allowed, message = phases.plan(number - 1, summary["workspace_actions"],
                                                      max_model_calls, max_actions, tools)
                request.update(tools=allowed, phase={"name": phase, "guidance": message})
            if pending_transition:
                request["edit_transition"] = transition.prompt
            call, action, stop = _model_call(model, request, number, deadline)
            if call is None:
                summary["stop_reason"] = stop
                break
            if pending_transition:
                summary["edit_transition"]["next_request"] = number
                summary["edit_transition"]["confirmed_sent"] = call.get("api", {}).get("edit_transition_sent", False)
                pending_transition = False
            usage.append((call["input_tokens"], call["output_tokens"]))
            summary["model_calls"] = number
            try:
                if action is not None and phases is not None and phase == "edit" and action["tool"] == "read_lines":
                    start, end = action["args"].get("start"), action["args"].get("end")
                    if type(start) is not int or type(end) is not int or not 1 <= start <= end < start + 200:
                        call.update(status="invalid_response", error="Edit-stage reads require start and end covering 1 to 200 lines")
                        action = None
                if action is not None:
                    if time.monotonic() >= deadline:
                        stop = "wall_time_budget"
                    else:
                        before_dispatch = time.monotonic()
                        response = runner.dispatch(action["tool"], action["args"], deadline=deadline)
                        call["tool_event_id"] = response["event_id"]
                        call["tool_status"] = response["status"]
                        if phases is not None:
                            phases.observe(action["tool"], response)
                        if action["tool"] == "finish" and response["status"] == "ok":
                            frozen = response["result"]
                            stopped_at = before_dispatch
                            stop = "model_finished"
                        else:
                            observation = {"tool": action["tool"], "event_id": response["event_id"],
                                           "observation": _observation(response, runner.run_dir)}
                            extra_objects = call.get("extra_objects", 0)
                            if extra_objects:
                                tool = action["tool"]
                                outcome = (f"Executed only the first action ({tool})." if response["status"] == "ok"
                                           else f"Attempted only the first action ({tool}), which returned an error.")
                                observation["action_feedback"] = (
                                    f"{outcome} Skipped {extra_objects} additional JSON objects. "
                                    "Use the tool result and return exactly one next action."
                                )
                            history.append(observation)
                            reason = transition.observe(action, response) if transition else None
                            if reason:
                                call["edit_transition_trigger"] = reason
                                summary["edit_transition"] = {"trigger_request": number, "reason": reason,
                                                              "next_request": None, "confirmed_sent": False}
                                pending_transition = True
                        # The tool layer is authoritative about charged operations.
                        state = json.loads((runner.controller / "state.json").read_text(encoding="utf-8"))
                        summary["workspace_actions"] = state["action_count"]
                elif stop is None:
                    history.append({"observation": _clip("Invalid model response: " + call["error"])})
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                call.update(controller_error=_error(exc))
                summary["controller_error"] = _error(exc)
                stop = "controller_error"
            finally:
                log.write(json.dumps(call) + "\n")
                log.flush()
            if stop:
                summary["stop_reason"] = stop
                break
        else:
            if time.monotonic() >= deadline:
                summary["stop_reason"] = "wall_time_budget"
            elif summary["workspace_actions"] >= max_actions:
                summary["stop_reason"] = "action_budget"
            else:
                summary["stop_reason"] = "model_call_budget"

    summary["solve_elapsed_seconds"] = round((stopped_at or time.monotonic()) - started, 6)
    input_tokens = [value for value, _ in usage if value is not None]
    output_tokens = [value for _, value in usage if value is not None]
    summary["usage"] = {
        "input_tokens": sum(input_tokens) if usage and len(input_tokens) == len(usage) else None,
        "output_tokens": sum(output_tokens) if usage and len(output_tokens) == len(usage) else None,
        "calls_with_usage": sum(a is not None and b is not None for a, b in usage),
    }
    if frozen is None:
        response = runner.dispatch("finish", {"reason": summary["stop_reason"]})
        if response["status"] != "ok":
            raise RuntimeError(response["error"])
        frozen = response["result"]
    return frozen


def run_baseline(source_archive, record_path, review_manifest, run_dir, python, git, model, *,
                 max_actions=30, max_model_calls=40, wall_time_seconds=1200,
                 expected_source_sha256=SOURCE_SHA256, skip_env_probe=False, edit_tool="apply_patch",
                 edit_transition=False, edit_phases=False):
    """Prepare a new run, freeze once on stopping, then evaluate the frozen patch."""
    limits = protocol()
    if edit_tool not in ("apply_patch", "replace_text"):
        raise ValueError("Unsupported workspace edit tool")
    if edit_phases and (edit_transition or edit_tool != "replace_text"):
        raise ValueError("Edit phases require replace_text and replace the one-time edit reminder")
    tools = TOOL_ARGUMENTS
    if edit_tool == "replace_text":
        tools = {
            (edit_tool if name == "apply_patch" else name):
            (REPLACE_TEXT_ARGUMENTS if name == "apply_patch" else description)
            for name, description in TOOL_ARGUMENTS.items()
        }
        limits.update(edit_tool=edit_tool, allowed_actions=list(tools))
    if edit_transition:
        limits["edit_transition"] = True
    if edit_phases:
        limits["edit_phases"] = {
            "locate_actions": EditPhases.LOCATE_ACTIONS, "locate_requests": EditPhases.LOCATE_REQUESTS,
            "directed_reads": EditPhases.EDIT_READS, "verify_requests": EditPhases.VERIFY_REQUESTS,
            "verify_actions": EditPhases.VERIFY_ACTIONS,
        }
    if type(max_actions) is not int or not 1 <= max_actions <= limits["max_actions"]:
        raise ValueError(f"max_actions must be between 1 and {limits['max_actions']}")
    if type(max_model_calls) is not int or max_model_calls < 1:
        raise ValueError("max_model_calls must be positive")
    if (type(wall_time_seconds) not in (int, float) or not math.isfinite(wall_time_seconds)
            or not 0 < wall_time_seconds <= limits["wall_time_seconds"]):
        raise ValueError(f"wall_time_seconds must be positive and at most {limits['wall_time_seconds']}")

    run_dir = Path(run_dir).absolute()
    prepare(source_archive, record_path, run_dir, python, git,
            expected_sha256=expected_source_sha256, skip_env_probe=skip_env_probe)
    limits.update(experiment_kind="single_task_baseline", max_actions=max_actions,
                  wall_time_seconds=wall_time_seconds, max_model_calls=max_model_calls,
                  finish_semantics="stop_and_freeze_operation; not_counted_towards_workspace_actions",
                  model_usage={"availability": "reported by adapter; null when unavailable"},
                  isolation={"os_sandbox": False, "description": "Checked tools in a native workspace"},
                  trace={"model_calls": "controller/model-calls.jsonl",
                         "workspace_actions": "controller/actions.jsonl"})
    summary = {
        "schema_version": 1, "run_id": run_dir.name, "status": "error", "resolved": False,
        "model": type(model).__name__, "edit_tool": edit_tool, "stop_reason": None,
        "model_calls": 0, "workspace_actions": 0,
        "budgets": {"max_actions": max_actions, "max_model_calls": max_model_calls,
                    "wall_time_seconds": wall_time_seconds, "test_timeout_seconds": limits["test_timeout_seconds"]},
        "context": {"history_window": HISTORY_WINDOW, "observation_max_chars": OBSERVATION_LIMIT},
        "evaluation": {"status": "not_run", "resolved": False},
    }
    stage = "activate"
    try:
        task = json.loads((run_dir / "input/task-input.json").read_text(encoding="utf-8"))
        summary.update(instance_id=task["instance_id"], base_commit=task["base_commit"])
        _write_json(run_dir / "controller/protocol.json", limits)
        (run_dir / "input/agent-instructions.md").write_text(INSTRUCTIONS + "\n", encoding="utf-8")
        runner = ToolRunner(run_dir)
        activate_run(run_dir)
        stage = "solve_and_freeze"
        frozen = _solve(runner, task, model, max_actions, max_model_calls, wall_time_seconds, summary,
                        tools=tools, transition=EditTransition(edit_tool) if edit_transition else None,
                        phases=EditPhases() if edit_phases else None)
        patch = Path(frozen["patch"])
        summary.update(patch_sha256=frozen["patch_sha256"], visible_tests=frozen["visible_tests"])
        if digest(patch) != frozen["patch_sha256"]:
            raise ValueError("Frozen patch SHA256 mismatch")
        stage = "evaluation"
        result = evaluate(source_archive, record_path, review_manifest, run_dir / "evaluation", python, git,
                          candidate_patch=patch, timeout_seconds=limits["test_timeout_seconds"],
                          expected_source_sha256=expected_source_sha256, skip_env_probe=skip_env_probe)
        evaluated_hash = result.get("input", {}).get("candidate_patch_sha256")
        if evaluated_hash is not None and evaluated_hash != frozen["patch_sha256"]:
            raise ValueError("Independent evaluation read a different candidate patch")
        grading = result.get("grading") or {}
        summary["evaluation"] = {
            "status": result["status"], "resolved": result["resolved"],
            **{key: grading.get(key) for key in ("f2p_passed", "f2p_total", "p2p_passed", "p2p_total")},
            "validation_errors": result.get("validation_errors", []),
        }
        if result["status"] == "completed" and "controller_error" not in summary:
            summary.update(status="completed", resolved=result["resolved"])
        else:
            summary["failure_stage"] = stage if result["status"] != "completed" else "solve_and_freeze"
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        summary.update(failure_stage=stage, error=_error(exc))
    if isinstance(model, DeepSeekModel):
        summary["api"] = model.summary()
    _write_json(run_dir / "summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-archive", "record-path", "review-manifest", "run-dir", "python", "git"):
        parser.add_argument("--" + name, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--mock-replies", type=Path, help="JSON array of offline model responses")
    modes.add_argument("--deepseek-budget-usd", type=Decimal,
                       help="Enable DeepSeek Flash using .env and the specified dollar budget")
    parser.add_argument("--edit-tool", choices=("apply_patch", "replace_text"), default="apply_patch")
    edit_mode = parser.add_mutually_exclusive_group()
    edit_mode.add_argument("--edit-transition", action="store_true", help="Suggest an edit after repeated localization")
    edit_mode.add_argument("--edit-phases", action="store_true", help="Limit localization and reserve verification budget")
    parser.add_argument("--max-actions", type=int, default=30)
    parser.add_argument("--max-model-calls", type=int, default=40)
    parser.add_argument("--wall-time-seconds", type=float, default=1200)
    options = vars(parser.parse_args(argv))
    replies_path = options.pop("mock_replies")
    budget = options.pop("deepseek_budget_usd")
    try:
        if budget is not None:
            from hj10.llm import policy_from_env

            model = DeepSeekModel(policy_from_env(task=""), budget)
        else:
            replies = json.loads(replies_path.read_text(encoding="utf-8-sig")) if replies_path else [
                {"tool": "list_files", "args": {"limit": 20}},
                {"tool": "read_lines", "args": {"path": "README.rst", "start": 1, "end": 20}},
                {"tool": "finish", "args": {"reason": "Offline smoke run complete"}},
            ]
            model = ScriptedModel(replies)
        result = run_baseline(**options, model=model)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "error", "resolved": False, "error": _error(exc)}))
        return 2
    print(json.dumps({"status": result["status"], "resolved": result["resolved"],
                      "stop_reason": result["stop_reason"], "evaluation": result["evaluation"],
                      "summary": str(Path(options["run_dir"]).absolute() / "summary.json")}))
    return 2 if result["status"] == "error" else 0 if result["resolved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
