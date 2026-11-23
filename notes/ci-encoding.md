# CI 编码：Windows cp1252

## 症状

Windows job 在 `mvp/tests` 失败，traceback 指向某个 `print`：

```text
UnicodeEncodeError: 'charmap' codec can't encode characters in position ...
```

输出可能显示 `[01] ????: list_files -> ok` 这种替换字符。

## 根因

- GitHub Actions 把 stdout 接到 pipe 时，Windows Python 使用 locale 编码
  （cp1252），不是 UTF-8；
- 中文进度行（`列出文件`、`状态：`）无法编码；
- 本地终端通常是 UTF-8，所以本地不报错。

## 修复顺序

1. **CI 层（最根本，已加 2026-11-24）**：`.github/workflows/check.yml`
   顶层已有：

   ```yaml
   env:
     PYTHONUTF8: "1"
     PYTHONIOENCODING: "utf-8"
   ```

   这覆盖父进程与子进程；即使新代码直接 `print` 中文也不会因 cp1252 崩。

2. **入口层**：`mvp/demo.py:main()` 与 `run.py:run()` 调用
   `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`。

3. **防御层**：`agent_core/agent.py:_emit` 与 `run.py:_emit` 在
   `UnicodeEncodeError` 时用 `errors="replace"` 重印。

## 不要做

- 不要为了 CI 把中文改成英文；报告与终端是中文用户界面。
- 不要在每个新 `print` 前手动判断编码；把输出集中到 runner/CLI。
- 不要只依赖 `_emit`；新增文件很容易忘记。

## 本地复现

```powershell
$env:PYTHONIOENCODING = 'cp1252'
python -B -m unittest discover -s mvp/tests
python -B mvp/run_demo.py
```

目前预期：`mvp` 12 OK、demo exit 0。
