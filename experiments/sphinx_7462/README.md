# Sphinx 7462 工作区准备、仓库工具与独立评测

Sphinx 7462 是 SWE-bench 收录的一道真实修复题，用于验证这套工具。

- `prepare.py` 从固定源码创建工作区，生成任务输入。
- `tools.py` 提供搜索、读取、补丁和测试操作，记录轨迹并保存最终补丁。
- `evaluate.py` 在独立副本中应用候选补丁和验收测试。
- `grading.py` 核对 pytest 日志和 JUnit，汇总需求与回归结果。

## 测试

在仓库根目录用 Python 3.9+、Git 和 pytest 8.3.3 运行。最近一次复测为 **39/39 通过**。

```sh
python -B -m unittest discover -s experiments/sphinx_7462/tests -v
```

## 真题校准

Windows 使用 Python 3.9.20 和[固定依赖](requirements-windows.txt)。将[源码归档](https://codeload.github.com/sphinx-doc/sphinx/tar.gz/b3e26a6c851133b82b50f4b68b53692076574d13)保存为 `mvp/runs/7462-inputs/sphinx-base.tar.gz`，同目录下的三个文件从 [Verified 实例](https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Verified&config=default&split=test&offset=391&length=1)取得。

- `record.json` 保存响应中 `rows[0].row` 的完整记录，实例 ID 应为 `sphinx-doc__sphinx-7462`。
- `review.json` 保留 `instance_id`、`repo`、`base_commit`，在 `evaluation` 中放入 `FAIL_TO_PASS` 和 `PASS_TO_PASS` 数组。将下方哈希填入 `reference_summary.test_patch_sha256_utf8`。
- `reference.patch` 保存记录的 `patch` 字段原文，使用 UTF-8 无 BOM。

```text
1745f2e0dfab31c3d3e871d528b1847969758b3aa457a5aa0145b50f647d29e1
```

```powershell
python -B -m experiments.sphinx_7462.evaluate `
  --source-archive mvp/runs/7462-inputs/sphinx-base.tar.gz `
  --record-path mvp/runs/7462-inputs/record.json `
  --review-manifest mvp/runs/7462-inputs/review.json `
  --python (Get-Command python).Source --git (Get-Command git).Source `
  --output-dir mvp/runs/7462-baseline
```

基线预期需求 **0/2**、回归 **49/49**，退出码为 `1`。用新输出目录重跑并加上 `--candidate-patch mvp/runs/7462-inputs/reference.patch`，预期需求 **2/2**、回归 **49/49**。结果见输出目录的 `evaluation.json`。

准备工作区和调用工具的参数可分别用 `python -m experiments.sphinx_7462.prepare --help` 和 `python -m experiments.sphinx_7462.tools --help` 查看。运行数据放在 `mvp/runs/`。[更新记录](../../CHANGELOG.md)
