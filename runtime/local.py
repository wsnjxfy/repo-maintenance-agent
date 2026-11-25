"""Local-machine Environment: filesystem, git and subprocess primitives.

Path safety (traversal, symlinks/reparse points, Windows reserved names) and
visibility (readable/editable/protected) are guaranteed here; handlers never
re-implement either rule.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from contracts.visibility import Visibility
from runtime import fsguard, process


class LocalEnvironment:
    def __init__(self, workspace, *, python=None, git="git", protected_paths=(),
                 visibility=None, scratch=None):
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("Workspace must be an existing directory")
        self.python = str(python) if python else sys.executable
        self.git = str(git) if git else "git"
        self.protected_paths = tuple(protected_paths)
        if visibility is None:
            visibility = Visibility(protected=tuple(protected_paths))
        self.visibility = visibility
        self.scratch = Path(scratch) if scratch else self.workspace.parent / ".scratch"
        self.scratch.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    # --- boundaries --------------------------------------------------------
    def resolve(self, path, allow_dot=False):
        return fsguard.safe_path(self.workspace, path, allow_dot=allow_dot)

    def is_readable(self, path):
        return self.visibility.allows_read(path)

    def is_editable(self, path):
        return self.visibility.allows_edit(path)

    def is_protected(self, name):
        return not self.visibility.allows_edit(name)

    def require_readable(self, path):
        if not self.is_readable(path):
            raise PermissionError("Path is outside the readable set: " + path)

    def require_editable(self, path):
        if not self.is_editable(path):
            raise PermissionError("Path is read-only or outside the editable set: " + path)

    # --- text/bytes --------------------------------------------------------
    def read_bytes(self, path):
        self.require_readable(path)
        return self.resolve(path).read_bytes()

    def write_bytes(self, path, data):
        self.require_editable(path)
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def read_text(self, path):
        return self.read_bytes(path).decode("utf-8")

    def write_text(self, path, content):
        self.write_bytes(path, content.encode("utf-8"))

    def exists(self, path):
        if not self.is_readable(path):
            return False
        return self.resolve(path, allow_dot=True).exists()

    def is_file(self, path):
        if not self.is_readable(path):
            return False
        return self.resolve(path, allow_dot=True).is_file()

    def delete(self, path):
        self.require_editable(path)
        self.resolve(path).unlink()

    def list_files(self, subdir="."):
        target = self.resolve(subdir, allow_dot=True)
        prefix = "" if target == self.workspace else target.relative_to(self.workspace).as_posix() + "/"
        names = [name for name in fsguard.files(self.workspace) if name.startswith(prefix)]
        if target.is_file():
            names = [target.relative_to(self.workspace).as_posix()]
        return sorted(self.visibility.filter(names))

    def manifest(self):
        return {name: digest for name, digest in fsguard.files(self.workspace).items()
                if self.is_readable(name)}

    # --- execution ---------------------------------------------------------
    def run(self, command, cwd=".", timeout=60, stdin=None, env_extra=None, logs=None):
        environment = process.worker_env(self.workspace, self.python)
        temp_dir = self.scratch / "tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        environment.update({"TEMP": str(temp_dir), "TMP": str(temp_dir)})
        environment.update(env_extra or {})
        cwd_path = self.workspace if cwd in ("", ".") else self.resolve(cwd, allow_dot=True)
        if stdin is not None:
            try:
                completed = subprocess.run(command, cwd=str(cwd_path), env=environment, input=stdin,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
            except subprocess.TimeoutExpired:
                return {"returncode": None, "stdout": "", "stderr": "Command exceeded its timeout",
                        "timed_out": True}
            return {"returncode": completed.returncode,
                    "stdout": completed.stdout.decode("utf-8", errors="replace"),
                    "stderr": completed.stderr.decode("utf-8", errors="replace"),
                    "timed_out": False}
        if logs is None:
            self._counter += 1
            logs = (self.scratch / ("run-%04d.stdout.log" % self._counter),
                    self.scratch / ("run-%04d.stderr.log" % self._counter))
        out_log, err_log = (Path(item) for item in logs)
        out_log.parent.mkdir(parents=True, exist_ok=True)
        result = process.process(command, cwd_path, environment, out_log, err_log, timeout)
        result["stdout"] = out_log.read_text(encoding="utf-8", errors="replace")
        result["stderr"] = err_log.read_text(encoding="utf-8", errors="replace")
        return result

    def export_patch(self, destination, baseline=None):
        """Export the full workspace diff against the initial commit."""
        environment = process.worker_env(self.workspace, self.python)
        add = subprocess.run([self.git, "add", "-A"], cwd=str(self.workspace), env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if add.returncode:
            raise ValueError("git add failed: " + add.stderr.decode("utf-8", errors="replace"))
        diff = subprocess.run([self.git, "diff", "--cached", "--binary", "--full-index", "HEAD"],
                              cwd=str(self.workspace), env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if diff.returncode:
            raise ValueError("git diff failed: " + diff.stderr.decode("utf-8", errors="replace"))
        Path(destination).write_bytes(diff.stdout)
