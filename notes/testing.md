# 测试与已知失败

## 五组测试

```powershell
python -B -m unittest discover -s agent_core/tests   # 23
python -B -m unittest discover -s tools/tests        # 8
python -B -m unittest discover -s runtime/tests      # 41（本机 2 个 git 2.37 失败）
python -B -m unittest discover -s mvp/tests          # 12
python -B -m unittest discover -s experiments/sphinx_7462/tests  # 56
```

## 已知本机失败（不是回归）

```text
runtime/tests/test_repo_backend.py::test_invalid_multi_file_patch_never_partially_applies
runtime/tests/test_repo_backend.py::test_patch_addition_survives_finish_and_applies_to_clean_base
```

原因：本机 git 2.37 的 patch 行为；CI 用较新 git 会过。不要在本地“修”
到 CI 红。

## 常见误判

| 现象 | 说明 |
| --- | --- |
| `python mvp/run_demo.py --max-steps 3` 失败 | 这是 demo 预算，预期 `budget_exhausted`；不是测试 |
| `test_mvp`/`test_demo` 在 CI Windows 报 `???` | cp1252，见 `ci-encoding.md` |
| tempfile 清理 `PermissionError` | DSH 沙箱的 ACL；在完整权限/正常临时目录跑 |

## 提交前

至少跑受影响的套件；改到 runner/toy 时跑 `mvp` + `agent_core`，改到
visibility/handler 时跑 `runtime` + `tools`。
