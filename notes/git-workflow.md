# Git / CI / 分支协作

## 当前分支

- 个人分支：`charles`（推送到 `origin/charles`）；
- `hxy` 是伙伴分支；合并时曾出现 8 个冲突（结构重命名 vs 旧路径），
  处理原则：**保留新结构，移植伙伴的功能**，不要整份取 incoming。

## 提交前

```powershell
git status --short
python -B -m unittest discover -s agent_core/tests
python -B -m unittest discover -s tools/tests
python -B -m unittest discover -s runtime/tests
python -B -m unittest discover -s mvp/tests
python -B -m unittest discover -s experiments/sphinx_7462/tests
```

确认没有 `.verify*`、`runs/`、`.env` 被加入。

## push 后

- GitHub Actions 同时跑 Ubuntu + Windows；Windows 常见编码问题见
  `ci-encoding.md`；
- 只有 CI 绿才算这一轮完成；
- 合并到 `charles` 后再考虑 `charles → main`；不要直接 force push。

## 分支/标签

- `archive/c1-toy-mvp`：C1 旧 toy 可回滚点；
- `integration/hxy`：历史整合分支（已合并）。
