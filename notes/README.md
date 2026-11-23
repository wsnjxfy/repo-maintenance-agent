# 项目笔记（notes）

给未来的人类与 agent 看的“踩坑记录 + 操作手册”。不是设计文档；设计在
`docs/interfaces*.md`，进度在 `docs/progress.md`。

| 文件 | 何时读 |
| --- | --- |
| `ci-encoding.md` | 改 CI、加 console 输出、Windows job 失败 |
| `windows-crlf.md` | 改 `replace_text`、Windows 下文本比對失败 |
| `testing.md` | 跑测试、判断失败是否已知 |
| `architecture.md` | 改分层、toolset、visibility、observation |
| `git-workflow.md` | 提交、push、CI、与 `hxy` 分支协作 |

规则：**同类失败第二次出现时，先更新对应笔记，再改代码。**

## 最小检查清单（提交前）

```powershell
python -B -m unittest discover -s agent_core/tests
python -B -m unittest discover -s tools/tests
python -B -m unittest discover -s runtime/tests
python -B -m unittest discover -s mvp/tests
python -B -m unittest discover -s experiments/sphinx_7462/tests
python -B mvp/run_demo.py
```
