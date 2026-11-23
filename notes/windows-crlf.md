# Windows CRLF 与 replace_text

## 背景

GitHub Actions Windows checkout 可能把文本文件转成 CRLF；本地仓库与
Ubuntu 通常是 LF。`replace_text` 的 `old` 片段如果按 LF 写，在 CRLF 文件
里会找不到。

## 现状

`tools/handlers.py:replace_text` 已做防御：

- 若文件是**纯 CRLF**（没有混合行尾），把 `old`/`new` 的 `\n` 正规化成
  `\r\n` 再比對；
- 写回保留原行尾。

对应测试：`tools/tests/test_handlers.py::test_crlf_checkout_accepts_lf_fragments`。

## 注意

- 不要假设 fixture 一定是 LF；测试要在 CRLF 下也能过。
- `apply_patch`（repo 路径）通过 `git apply`，由 git 处理行尾；toy 现在
  用 `replace_text`。
- 新增文本工具时，先问：输入片段来自模型（LF）还是文件（可能 CRLF）？
