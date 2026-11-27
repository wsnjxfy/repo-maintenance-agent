# 四个 SWE-bench Verified 开发样例

这四个样例通过同一个 Web 和 CLI 任务注册表启动。每题固定 Verified 的原始代码和题面，并带有两条项目维护的检查。一条检查题面行为，一条检查附近的正常行为。

| 统一入口 ID | Verified 实例与基线 | 需求检查 | 回归检查 |
| --- | --- | --- | --- |
| `flask-5014` | `pallets__flask-5014`，`7ee9ceb71e86`，[原题 #5010](https://github.com/pallets/flask/issues/5010) | 空名称创建 Blueprint 应抛出 `ValueError`。 | 非空名称仍可正常创建。 |
| `sympy-23262` | `sympy__sympy-23262`，`fdc707f73a65`，[原题 #23224](https://github.com/sympy/sympy/issues/23224) | 调用 `lambdify` 生成的函数后，单元素元组应返回 `(1,)`。 | 双元素元组仍返回 `(1, 2)`。 |
| `pytest-10051` | `pytest-dev__pytest-10051`，`aa55975c7d3f`，[原题 #9877](https://github.com/pytest-dev/pytest/issues/9877) | `caplog.clear()` 后，两种日志查询都应清空，再记一条只能看见新日志。 | 清空前，两种查询都应取得同一条日志。 |
| `pylint-7080` | `pylint-dev__pylint-7080`，`3c5eca2ded3d`，[原题 #6964](https://github.com/pylint-dev/pylint/issues/6964) | 从当前目录递归检查时，`ignore-paths` 中的生成目录应被排除，普通文件仍应产生诊断。 | 去掉排除配置后，两处的样例文件均会产生诊断。 |

检查代码放在 `checks/`，只由控制端在独立副本中加载。数据来源是 [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified)。这些分数对应表中的两条检查，完整官方 F2P/P2P 验收需要另行校准。

## 本地准备

用仓库的 Python 运行准备命令。省略 `--task` 会准备全部四套数据。准备命令只下载固定归档和数据并核对摘要，不执行样例。

```powershell
python -m experiments.verified_samples.prepare --task flask-5014
```

归档、题面和参考补丁保存在 Git 忽略的 `data/<instance_id>/`。每题使用独立的 Python 3.9 环境，依赖版本在 `specs.py`。以 Flask 为例：

```powershell
$sampleDir = 'data/pallets__flask-5014'
py -3.9 -m venv "$sampleDir/.venv"
python -c "from experiments.verified_samples.specs import SAMPLES; print('\n'.join(SAMPLES['flask-5014'].requirements))" | Set-Content -Encoding ascii "$sampleDir/requirements.txt"
& "$sampleDir/.venv/Scripts/python.exe" -m pip install -r "$sampleDir/requirements.txt"
```

pytest 这题的运行器取自固定源码的 `src` 目录，该题的依赖清单因此不安装另一个 pytest。已有独立解释器时，CLI 可使用 `--python` 指定。

Pylint 的上游源码归档包含两条用于其他测试的符号链接，位于 `tests/functional/s/symlink/_binding/`。准备脚本只在两个路径和目标都与已核对的值匹配时略过它们，并在 `freeze.json` 记录原归档摘要和略过项。Pylint 样例用自己的临时目录验证递归扫描。

## 执行入口

本次仅准备和上传样例。需要校准时，可以通过统一入口执行。`none` 评测原始代码，`reference` 评测冻结参考补丁。两个策略都不调用模型。

```powershell
python -B -m scripts.run_task --task flask-5014 --policy none
python -B -m scripts.run_task --task flask-5014 --policy reference
```

预期校准结果是需求检查在原版失败、参考补丁通过，回归检查在两份都通过。以实际报告和日志为准。四题的对照尚未执行。
