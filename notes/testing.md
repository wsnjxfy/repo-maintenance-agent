# 测试与已知失败

## 核心五组 + 门户套件

```powershell
python -B -m unittest discover -s agent_core/tests   # 核心
python -B -m unittest discover -s tools/tests        # 目录 / 工具子集守门
python -B -m unittest discover -s runtime/tests
python -B -m unittest discover -s mvp/tests
python -B -m unittest discover -s experiments/sphinx_7462/tests
python -B -m unittest discover -s web/tests          # 调试门户（registry / CLI / server）
```

前五组是 AGENTS.md 规定的核心套件，标准库即可运行。`web/tests` 覆盖 Web
门户（`tasks/registry.py`、`scripts/run_task.py`、`web/server.py`）；其中
server 测试需要 `fastapi`/`httpx`（见 `requirements-web.txt`），门户以外的代码
仍保持零依赖。

## 已知本机失败

- 旧 `test_repo_backend.py` 的 2 个 git 2.37 失败随 `ToolRunner` 一起移除；
  若重新引入等价 patch 测试，仍应按 git 版本判定。
- 本机（Linux，`/usr/bin/python3` 未装 pytest）`runtime` 的
  `test_vertical_slice.test_read_patch_test_finish` 与 `experiments/sphinx_7462`
  的 4 个用例失败，是环境缺 pytest / py3.9 venv 所致，与门户改动无关；门户的
  preflight 会把这类环境问题显示成红灯，避免误判成“模型没修好”。

## 常见误判

| 现象 | 说明 |
| --- | --- |
| `python mvp/run_demo.py --max-steps 3` 失败 | 这是 demo 预算，预期 `budget_exhausted`；不是测试 |
| `test_mvp`/`test_demo` 在 CI Windows 报 `???` | cp1252，见 `ci-encoding.md` |
| CI Windows 测试路径 `RUNNER~1` 与 `runneradmin` 不相等 | `Path.resolve()` 会展开 8.3 短名；比较 `resolve()` 后的路径，不要比较原始字符串 |
| tempfile 清理 `PermissionError` | DSH 沙箱的 ACL；在完整权限/正常临时目录跑 |

## 提交前

至少跑受影响的套件；改到 runner/toy 时跑 `mvp` + `agent_core`，改到
visibility/handler 时跑 `runtime` + `tools`。
