# Tool versioning

`tools/specs.py` is the single source of truth for each tool's `version`
integer. It starts at `0` (`v0` in the UI/report).

## When to bump

Bump the affected tool's `version` in the **same commit** when changing any of:

- `tools/handlers.py` behavior for that tool;
- the tool's arguments, return/observation shape, or error semantics;
- the protected-path/visibility behavior of that tool;
- a bug fix that changes what the tool does.

Do **not** bump for:

- documentation/comment-only edits;
- label or prompt wording with no behavior change;
- adding a new, unused tool (start it at `0`).

## Run snapshot

`run.py` calls `tools.toolsets.tool_versions(names)` at run start and writes
the result to `summary.json.tool_versions`. The web portal stores the same
snapshot in `runs/web/<id>/web-run.json` so history can show it even before a
summary is written.

Old runs without the field are shown as unknown (`—`); do not backfill with the
current versions.

## Checks

```powershell
python -B -m unittest discover -s tools/tests
```

`tools/tests/test_versions.py` checks that every spec has a non-negative
integer version and that the snapshot helper only includes requested tools.
There is no automatic "did you bump it?" check; this remains a review rule.
