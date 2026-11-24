# 测试与已知失败

## 五组测试

```powershell
python -B -m unittest discover -s agent_core/tests   # 28
python -B -m unittest discover -s tools/tests        # 8
python -B -m unittest discover -s runtime/tests      # 18
python -B -m unittest discover -s mvp/tests          # 12
python -B -m unittest discover -s experiments/sphinx_7462/tests  # 37
```

## 已知本机失败

无。旧 `test_repo_backend.py` 的 2 个 git 2.37 失败随 `ToolRunner` 一起移除；
若重新引入等价 patch 测试，仍应按 git 版本判定。

## 常见误判

| 现象 | 说明 |
| --- | --- |
| `python mvp/run_demo.py --max-steps 3` 失败 | 这是 demo 预算，预期 `budget_exhausted`；不是测试 |
| `test_mvp`/`test_demo` 在 CI Windows 报 `???` | cp1252，见 `ci-encoding.md` |
| tempfile 清理 `PermissionError` | DSH 沙箱的 ACL；在完整权限/正常临时目录跑 |

## 提交前

至少跑受影响的套件；改到 runner/toy 时跑 `mvp` + `agent_core`，改到
visibility/handler 时跑 `runtime` + `tools`。
