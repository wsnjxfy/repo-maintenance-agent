# Sphinx 7462 工作区准备、仓库工具与独立评测

Sphinx 7462 是 SWE-bench 收录的一道真实修复题，用于验证这套工具。

- `prepare.py` 从固定源码创建工作区，生成任务输入。
- 仓库工具已上移到共享层 `runtime/backends/repo.py`（搜索、读取、补丁和测试操作，记录轨迹并保存最终补丁）。
- `baseline.py` 用模拟模型串起工作区准备、工具执行、补丁冻结和独立验收。
- `evaluate.py` 在独立副本中应用候选补丁和验收测试。
- `grading.py` 核对 pytest 日志和 JUnit，汇总需求与回归结果。

## 测试

在仓库根目录用 Python 3.9+、Git 和 pytest 8.3.3 运行。

```sh
python -B -m unittest discover -s runtime/tests -v
python -B -m unittest discover -s experiments/sphinx_7462/tests -v
```

## 真题校准

Windows 使用 Python 3.9.20 和[固定依赖](requirements-windows.txt)。将[源码归档](https://codeload.github.com/sphinx-doc/sphinx/tar.gz/b3e26a6c851133b82b50f4b68b53692076574d13)保存为 `data/sphinx-doc__sphinx-7462/sphinx-base.tar.gz`，同目录下的三个文件从 [Verified 实例](https://datasets-server.huggingface.co/rows?dataset=princeton-nlp%2FSWE-bench_Verified&config=default&split=test&offset=391&length=1)取得。

- `record.json` 保存响应中 `rows[0].row` 的完整记录，实例 ID 应为 `sphinx-doc__sphinx-7462`。
- `review.json` 保留 `instance_id`、`repo`、`base_commit`，在 `evaluation` 中放入 `FAIL_TO_PASS` 和 `PASS_TO_PASS` 数组。将下方哈希填入 `reference_summary.test_patch_sha256_utf8`。
- `reference.patch` 保存记录的 `patch` 字段原文，使用 UTF-8 无 BOM。

```text
1745f2e0dfab31c3d3e871d528b1847969758b3aa457a5aa0145b50f647d29e1
```

```powershell
python -B -m experiments.sphinx_7462.evaluate `
  --source-archive data/sphinx-doc__sphinx-7462/sphinx-base.tar.gz `
  --record-path data/sphinx-doc__sphinx-7462/record.json `
  --review-manifest data/sphinx-doc__sphinx-7462/review.json `
  --python (Get-Command python).Source --git (Get-Command git).Source `
  --output-dir runs/sphinx-7462-baseline
```

基线预期需求 **0/2**、回归 **49/49**，退出码为 `1`。用新输出目录重跑并加上 `--candidate-patch data/sphinx-doc__sphinx-7462/reference.patch`，预期需求 **2/2**、回归 **49/49**。结果见输出目录的 `evaluation.json`。

## 离线 Agent 基线

沿用上面的源码、任务记录和评测环境。每次运行指定一个新目录。

```powershell
python -B -m experiments.sphinx_7462.baseline `
  --source-archive data/sphinx-doc__sphinx-7462/sphinx-base.tar.gz `
  --record-path data/sphinx-doc__sphinx-7462/record.json `
  --review-manifest data/sphinx-doc__sphinx-7462/review.json `
  --python (Get-Command python).Source --git (Get-Command git).Source `
  --run-dir runs/sphinx-7462-mock-001
```

默认模拟响应只浏览工作区并结束，冻结的是空补丁。预期需求 **0/2**、回归 **49/49**。可用 `--mock-replies 文件.json` 指定响应序列。文件是 JSON 数组，每项可用 `{"tool":"list_files","args":{}}` 这样的动作对象，或用字符串模拟模型返回的原始文本。

解题默认限 30 次工作区动作、40 次模拟模型调用和 20 分钟。可见测试的 pytest 进程单次最多 90 秒，也受解题剩余时间约束。准备、冻结和独立验收在解题计时之外。传给模拟模型的工具反馈按字符限制，token 用量记为未知。

- `summary.json` 汇总停止原因、预算消耗、冻结补丁和验收结果。
- `controller/model-calls.jsonl` 记录模拟请求和响应，`controller/actions.jsonl` 记录工具动作。
- `controller/submission/final.patch` 保存冻结补丁，`evaluation/evaluation.json` 保存独立判定。

模型响应先走严格 JSON 校验。失败时，控制器尝试提取第一个 JSON 对象并归一化参数。恢复的动作仍由 Sphinx 工具校验。响应含多个对象时，下一轮会看到首个动作的执行结果和略过的对象数。日志记录解析路径及传给模型的反馈。

加 `--edit-tool replace_text` 可改用唯一片段替换。参数为现有文件的相对路径 `path`、恰好匹配一次的原文 `old` 和替换文本 `new`。控制器在停止后统一导出补丁，供独立评测使用。

加 `--edit-transition` 可在首次成功编辑前启用一次提醒。成功读取源码后，累计两次读取已覆盖范围或重复零匹配搜索，下一次请求会提示模型判断是否已有足够依据做一次小改动。提醒计入原有输入额度，触发和发送情况会写入运行日志。

加 `--edit-tool replace_text --edit-phases` 可使用定位、编辑和验证阶段。定位最多 8 次工作区动作或 12 次模型请求，先到为准。编辑阶段可以定向读取两次，每次明确文件与不超过 200 行的范围。每次成功编辑后先运行可见测试。剩余 4 次请求或 2 次动作时进入最终验证阶段，只能运行可见测试或结束。

退出码 `0` 表示已解决，`1` 表示评测有效但未解决，`2` 表示流程或评测错误。

## DeepSeek Flash 试跑

沿用上面的命令，换一个新运行目录，加上 `--deepseek-budget-usd 0.50`。用 Python 3.10+ 启动控制器，`--python` 仍指向已校准的 Sphinx Python 3.9 环境。工作区根目录的 `.env` 提供 `LLM_API_KEY`、`LLM_BASE_URL=https://api.deepseek.com`、`LLM_MODEL=deepseek-flash` 和 `LLM_JSON_MODE=on`。单次输出上限为 4096 token。

控制器复用 `agent_core/llm.py` 的 Chat Completions 请求层。每次调用前按[高峰期价格](https://api-docs.deepseek.com/quick_start/pricing/)预留费用。请求后按服务端用量记账，缺失用量时停止追加请求。模型调用日志保存响应中的模型名、token、缓存命中和耗时，汇总里的费用是按高峰单价计算的估算值。

准备工作区和调用工具的参数可分别用 `python -m experiments.sphinx_7462.prepare --help` 和 `python -m runtime.backends.repo --help` 查看。运行数据放在 `runs/`。[更新记录](../../CHANGELOG.md)
