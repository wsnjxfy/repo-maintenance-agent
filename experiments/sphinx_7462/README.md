# Sphinx 7462 工作区、统一 runner 与独立评测

Sphinx 7462 是 SWE-bench 收录的一道真实修复题，用于验证共享 agent 栈。

- `prepare.py`：从固定源码创建工作区、baseline 与 manifest；`prepare_workspace()` 供 Task 复用。
- `task.py`：`SphinxTask`，把 fixture、visibility、protocol、`prepare()` / `evaluate()` 接给 `run.py`。
- `runner.py`：用 `run.py + SphinxTask` 跑 `reference` / `none` / `llm` 三种策略。
- `oracle.py`：`ReferencePatchPolicy`，回放 `reference.patch` 做校准。
- `evaluate.py`：在独立副本中应用候选补丁和官方测试。
- `grading.py`：核对 pytest 日志与 JUnit，汇总需求/回归结果。

旧 `baseline.py` 与 `runtime/backends/repo.py`（`ToolRunner`）已移除；历史版本见
`git tag archive/sphinx-toolrunner-baseline`。

## 测试

```sh
python -B -m unittest discover -s experiments/sphinx_7462/tests -v
```

真实校準使用 `experiments/sphinx_7462/.venv39`（Python 3.9.20、pytest 8.3.3）。

## 真题输入

把固定源码归档保存为 `data/sphinx-doc__sphinx-7462/sphinx-base.tar.gz`，同目录放
`record.json`、`review.json`、`reference.patch`。`data/` 与 `runs/` 已被 gitignore。

旧路径说明：`--review-manifest` 是参数名称，实际文件名可以是 `review.json`。

## 独立评测

```powershell
python -B -m experiments.sphinx_7462.evaluate `
  --source-archive data/sphinx-doc__sphinx-7462/sphinx-base.tar.gz `
  --record-path data/sphinx-doc__sphinx-7462/record.json `
  --review-manifest data/sphinx-doc__sphinx-7462/review.json `
  --python experiments/sphinx_7462/.venv39/Scripts/python.exe `
  --git (Get-Command git).Source `
  --output-dir runs/sphinx-eval-base
```

空补丁预期需求 **0/2**、回归 **49/49**，退出码 `1`。加上
`--candidate-patch data/sphinx-doc__sphinx-7462/reference.patch` 预期 **2/2**、**49/49**、退出码 `0`。

## 统一 runner

`run.py + SphinxTask` 的三条路径：

```powershell
# reference：2/2、49/49
python -B -m experiments.sphinx_7462.runner `
  --source-archive data/sphinx-doc__sphinx-7462/sphinx-base.tar.gz `
  --record-path data/sphinx-doc__sphinx-7462/record.json `
  --review-manifest data/sphinx-doc__sphinx-7462/review.json `
  --python experiments/sphinx_7462/.venv39/Scripts/python.exe `
  --git (Get-Command git).Source `
  --policy reference --output-dir runs/sphinx-agent-ref

# 空补丁：0/2、49/49
python -B -m experiments.sphinx_7462.runner ... --policy none --output-dir runs/sphinx-agent-none

# 真实模型：需要 .env 的 LLM_*；预算必须先设价格
python -B -m experiments.sphinx_7462.runner ... --policy llm `
  --model deepseek-flash --budget-usd 0.50 --max-total-tokens 300000 `
  --output-dir runs/sphinx-agent-llm
```

运行产物在 `runs/<prefix>-.../`：`summary.json`、`trajectory.jsonl`、`final.patch`、
`evaluation/evaluation.json`、`report.html`；模型模式另有 `llm_calls.jsonl`。

预算模式必须同时在 `.env` 设置 `LLM_PRICE_INPUT` / `LLM_PRICE_OUTPUT`（USD / 1M tokens），
否则 `--budget-usd` 会被拒绝，避免用 0 价格假装有上限。

## 说明

`evaluate()` 会重新解一份干净 workspace，agent 的 workspace 不参与评分；候选补丁由
`Session.finish` 或 `Session.finalize()` 导出。受保护测试/配置由 `Visibility` 阻挡。
