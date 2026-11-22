"""Subprocess execution with timeouts and process-tree termination."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time


def worker_env(workspace: Path, python: str) -> dict[str, str]:
    names = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL", "NUMBER_OF_PROCESSORS"}
    env = {k: v for k, v in os.environ.items() if k.upper() in names}
    env.update({"PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(workspace), "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    env["PATH"] = str(Path(python).parent) + os.pathsep + env.get("PATH", "")
    return env


def time_left(deadline: float | None) -> float:
    if deadline is None:
        return float("inf")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Trial wall-time limit reached")
    return remaining


def process(command, cwd, env, stdout, stderr, timeout):
    started = time.monotonic()
    kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    timed_out = False
    cleanup = None
    with stdout.open("wb") as out, stderr.open("wb") as err:
        child = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL, **kwargs)
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                taskkill = str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe")
                killed = subprocess.run([taskkill, "/PID", str(child.pid), "/T", "/F"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                cleanup = {"method": "taskkill_tree", "returncode": killed.returncode,
                           "output": killed.stdout.decode("utf-8", errors="replace")}
            else:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                cleanup = {"method": "kill_process_group"}
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
                cleanup["fallback"] = "kill_parent"
    return {"returncode": child.returncode, "timed_out": timed_out,
            "elapsed_seconds": round(time.monotonic() - started, 4), "cleanup": cleanup}
