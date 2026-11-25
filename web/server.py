"""FastAPI debug portal: configure a run in the browser, watch it live.

The browser never runs the agent. Each run is a child process of
``scripts.run_task`` launched into its own ``runs/web/<id>/`` directory; the
server streams the child's stdout and the run's ``trajectory.jsonl`` over SSE and
serves the finished artifacts (``report.html``, ``summary.json`` …).

Safety (see AGENTS.md and the plan's risk list):

- Binds **127.0.0.1 only** — this executes arbitrary repository code.
- The model API key stays server-side; the child reads it from ``.env``.
- Artifact paths are confined to the run directory (``..`` is rejected).
- Child stdout is read as UTF-8 (``errors="replace"``) and the child runs with
  ``PYTHONUTF8=1`` so Chinese console output survives.
- Concurrency is capped (default 1) so heavy tasks do not fight for the CPU.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tasks.registry import TASKS, get_entry
from tools.specs import TOOL_SPECS
from tools.toolsets import available_tools, validate_tool_names

REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNS_ROOT = REPO_ROOT / "runs" / "web"
EDIT_TOOLS = ("replace_text", "apply_patch")
REQUIRED_TOOL = "finish"
MAX_CONCURRENT = 1

app = FastAPI(title="HJ10 repo-maintenance portal")


class RunRecord:
    def __init__(self, web_run_id, config, output_dir):
        self.id = web_run_id
        self.config = config
        self.output_dir = output_dir
        self.created = time.time()
        self.status = "running"      # running | done | error | cancelled
        self.returncode = None
        self.proc = None
        self.log_lines = []
        self.done = threading.Event()

    def asdict(self):
        return {
            "id": self.id,
            "config": self.config,
            "created": self.created,
            "status": self.status,
            "returncode": self.returncode,
            "run_dir": self._run_subdir_name(),
            "summary": self.summary(),
        }

    # The actual run lands in a single child dir of output_dir.
    def run_subdir(self):
        if not self.output_dir.is_dir():
            return None
        children = [p for p in self.output_dir.iterdir() if p.is_dir()]
        return children[0] if len(children) == 1 else None

    def _run_subdir_name(self):
        sub = self.run_subdir()
        return sub.name if sub else None

    def summary(self):
        sub = self.run_subdir()
        if sub is None:
            return None
        path = sub / "summary.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return None

    def steps(self):
        sub = self.run_subdir()
        if sub is None:
            return []
        path = sub / "trajectory.jsonl"
        if not path.is_file():
            return []
        events = []
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                obs = event.get("observation") or {}
                events.append({
                    "step": event.get("step"),
                    "tool": (event.get("action") or {}).get("tool"),
                    "ok": obs.get("ok") if isinstance(obs, dict) else None,
                    "phase": event.get("phase"),
                })
        except (ValueError, OSError):
            pass
        return events


RUNS = {}
RUNS_LOCK = threading.Lock()


def _active_runs():
    return [r for r in RUNS.values() if r.status == "running"]


def _reader(record):
    """Drain child stdout into the record and a durable web.log, then finalize."""
    log_path = record.output_dir / "web.log"
    try:
        with log_path.open("w", encoding="utf-8") as handle:
            for raw in record.proc.stdout:
                line = raw.rstrip("\n")
                record.log_lines.append(line)
                handle.write(line + "\n")
                handle.flush()
    finally:
        record.returncode = record.proc.wait()
        if record.status == "running":
            record.status = "done" if record.returncode in (0, 1) else "error"
        record.done.set()


def _launch(record, config):
    cmd = [sys.executable, "-B", "-m", "scripts.run_task",
           "--task", config["task"],
           "--policy", config["policy"],
           "--tools", ",".join(config["tools"]),
           "--output-dir", str(record.output_dir)]
    if config.get("model"):
        cmd += ["--model", config["model"]]
    if config.get("max_steps"):
        cmd += ["--max-steps", str(config["max_steps"])]
    if config.get("budget_usd"):
        cmd += ["--budget-usd", str(config["budget_usd"])]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    record.proc = subprocess.Popen(
        cmd, cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", bufsize=1,
        start_new_session=True)
    threading.Thread(target=_reader, args=(record,), daemon=True).start()


# --- request models ---------------------------------------------------------

class RunRequest(BaseModel):
    task: str
    tools: list[str] = []
    policy: str = "scripted"
    model: str | None = None
    max_steps: int | None = None
    budget_usd: float | None = None


# --- catalog & preflight ----------------------------------------------------

@app.get("/api/catalog")
def catalog():
    tools = []
    for name in available_tools():
        spec = TOOL_SPECS[name]
        tools.append({
            "name": name,
            "label": spec["label"],
            "doc": spec["doc"],
            "mutating": spec["mutating"],
            "executes_code": spec["executes_code"],
            "is_edit": name in EDIT_TOOLS,
            "required": name == REQUIRED_TOOL,
        })
    return {"tasks": [entry.asdict() for entry in TASKS.values()],
            "tools": tools,
            "edit_tools": list(EDIT_TOOLS),
            "required_tool": REQUIRED_TOOL}


@app.get("/api/tasks/{task_id}/preflight")
def preflight(task_id: str, policy: str = "scripted", budget_usd: float | None = None):
    try:
        entry = get_entry(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"checks": entry.preflight({"policy": policy, "budget_usd": budget_usd})}


# --- run lifecycle -----------------------------------------------------------

def _normalize_tools(entry, requested):
    tools = list(requested) if requested else list(entry.default_tools)
    if REQUIRED_TOOL not in tools:
        tools.append(REQUIRED_TOOL)
    if not any(t in EDIT_TOOLS for t in tools):
        raise HTTPException(status_code=400,
                            detail="至少需要一个编辑工具（replace_text 或 apply_patch）")
    try:
        return validate_tool_names(tools)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/runs")
def create_run(req: RunRequest):
    try:
        entry = get_entry(req.task)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if req.policy not in entry.policies:
        raise HTTPException(status_code=400,
                            detail=f"policy {req.policy!r} 不适用于 {req.task!r}")
    tools = _normalize_tools(entry, req.tools)
    with RUNS_LOCK:
        if len(_active_runs()) >= MAX_CONCURRENT:
            raise HTTPException(status_code=429, detail="已有运行中的任务，请先等待或取消。")
        web_run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        output_dir = RUNS_ROOT / web_run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        config = {"task": req.task, "tools": tools, "policy": req.policy,
                  "model": req.model, "max_steps": req.max_steps,
                  "budget_usd": req.budget_usd}
        record = RunRecord(web_run_id, config, output_dir)
        RUNS[web_run_id] = record
        _launch(record, config)
    return {"id": web_run_id, "config": config}


@app.get("/api/runs")
def list_runs():
    with RUNS_LOCK:
        records = sorted(RUNS.values(), key=lambda r: r.created, reverse=True)
        return {"runs": [r.asdict() for r in records]}


def _get_record(run_id):
    record = RUNS.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return record


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return _get_record(run_id).asdict()


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    record = _get_record(run_id)
    if record.status == "running" and record.proc and record.proc.poll() is None:
        record.status = "cancelled"
        try:
            os.killpg(os.getpgid(record.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    return {"id": run_id, "status": record.status}


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/runs/{run_id}/stream")
def stream_run(run_id: str):
    record = _get_record(run_id)

    def events():
        yield _sse("open", {"id": record.id, "config": record.config})
        sent_lines = 0
        sent_steps = 0
        while True:
            lines = record.log_lines
            while sent_lines < len(lines):
                yield _sse("log", {"line": lines[sent_lines]})
                sent_lines += 1
            steps = record.steps()
            while sent_steps < len(steps):
                yield _sse("step", steps[sent_steps])
                sent_steps += 1
            if record.done.is_set() and sent_lines >= len(record.log_lines):
                # Flush any late steps the final trajectory write exposed.
                steps = record.steps()
                while sent_steps < len(steps):
                    yield _sse("step", steps[sent_steps])
                    sent_steps += 1
                yield _sse("done", {"status": record.status,
                                    "returncode": record.returncode,
                                    "run_dir": record._run_subdir_name(),
                                    "summary": record.summary()})
                break
            time.sleep(0.3)

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}/artifacts/{path:path}")
def artifact(run_id: str, path: str):
    record = _get_record(run_id)
    sub = record.run_subdir()
    if sub is None:
        raise HTTPException(status_code=404, detail="run directory not ready")
    target = (sub / path).resolve()
    try:
        target.relative_to(sub.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes the run directory")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(str(target))


# --- static UI (mounted last so /api/* wins) --------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


app.mount("/", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main(argv=None):
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost"):
        parser.error("Refusing to bind a code-executing debug server to a non-loopback host.")
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"HJ10 portal: http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
