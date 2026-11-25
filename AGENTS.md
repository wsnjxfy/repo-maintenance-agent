# Agent instructions (read this first)

This repository is a research scaffold for a repository-maintenance agent
(HJ10). Before changing CI, runner output, tests, or layer boundaries, read
`notes/README.md` and the relevant note.

## Non-negotiable rules

- **Encoding**: Windows CI stdout is cp1252. Chinese console output must go
  through an encoding-safe emitter (`_emit`) or UTF-8 stdout configuration.
  Do **not** "fix" this by removing Chinese from user-facing output.
- **Core test suites** (standard library only):
  `agent_core/tests`, `tools/tests`, `runtime/tests`, `mvp/tests`,
  `experiments/sphinx_7462/tests`. Run the relevant ones before committing.
  The optional debug portal adds `web/tests` (needs `requirements-web.txt`);
  run it when touching `tasks/`, `scripts/` or `web/`.
- **Known local-only failures**: none after removing the legacy
  `ToolRunner` tests. If equivalent patch tests are reintroduced, check git
  version instead of weakening assertions.
- **`--max-steps 3` is a demo budget**, not a test command. Tests use
  `python -B -m unittest discover -s ...`.
- **Layering**: `contracts/` imports no project module; `tools/` must not
  import `runtime/`; `agent_core/` must not import `runtime/`, `evaluation/`
  or `experiments/`; hidden evaluation material never enters the agent side.
- **Toolset vs visibility**: a toolset only chooses tool names; readable /
  editable / protected paths belong to the instance (`Visibility`), enforced
  by `LocalEnvironment`, never by handlers or toolsets.
- **Tool versions**: each `TOOL_SPECS` entry has an integer `version` starting
  at `0`; bump the affected tool in the same commit as a behavior/handler
  change. `run.py` snapshots enabled versions into `tool_versions`; see
  `notes/tool-versioning.md`.
- **Legacy removed**: `runtime/backends/repo.py` (`ToolRunner`) and
  `experiments/sphinx_7462/baseline.py` are gone. Historical code is at
  `git tag archive/sphinx-toolrunner-baseline`; do not reintroduce it.

## Where to look

- `notes/ci-encoding.md` — cp1252 / UTF-8 CI failures and fixes
- `notes/windows-crlf.md` — CRLF checkout and `replace_text`
- `notes/testing.md` — suites, commands, known failures
- `notes/architecture.md` — layers, observation contract, current migration state
- `notes/git-workflow.md` — branches, commits, CI, hxy merge lessons
- `docs/interfaces.md` — frozen v1 contracts
- `docs/interfaces-v2-draft.md` — v2 design (r2.1)
