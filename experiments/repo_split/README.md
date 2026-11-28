# 15 仓库开发集和留测集

任务材料位于本地 `data/repo-split-15-20261128/`。其中 10 个仓库用于开发，5 个仓库用于留测。保留 test 组用于流程与预算固定后的验证。

## 首次在本机准备

仓库里有 15 题的公开目录、基线版本和依赖清单。选择一题时，只需要下载并准备那一题。以下例子在仓库根目录执行：

```powershell
python -m pip install duckdb
python -B -m experiments.repo_split.prepare --task dev-flask-5014
python -B -m experiments.repo_split.runtime --task dev-flask-5014
python -B -m scripts.run_task --task dev-flask-5014 --policy llm --preflight
```

数据准备从固定版本的公开数据集和上游 Git 提交下载材料。运行环境准备需要本机的 `uv`。Verified 题使用 Python 3.9，三道 Live 题使用 Python 3.12。已有解释器时，可以向 `runtime` 传 `--python <路径>`。已有专用环境时，可以用 `--probe-only` 只更新源码导入检查。当前依赖清单来自 Windows 本机环境，其他系统先确认依赖和来源检查能够通过。

新克隆会显示全部任务。未准备材料的题会在预检中提示缺项。已有四题入口和分组内的任务入口分别使用各自的本地数据。运行 `llm` 前按仓库的 `.env.example` 配置本地模型连接。

## 统一入口

在 Web 的任务列表中选择 `dev（开发集）` 或 `test（留测集）`。CLI 使用带分组前缀的任务 ID。以下命令只检查环境和配置，不启动任务：

```powershell
python -B -m scripts.run_task --task dev-flask-5014 --policy llm --preflight
python -B -m scripts.run_task --task test-tablib-613 --policy llm --preflight
```

三个策略为 `none`、`reference` 和 `llm`。默认预检核对 Git、固定源码、独立 Python 解释器和真实源码导入的记录，以及控制端补丁的校验值。`llm` 还检查本地模型配置。填写美元预算时会额外检查输入与输出价格。

## 环境与评测边界

每题有独立环境。依赖约束、解析出的依赖版本、安装日志和源码导入证明都保存在本地 `runtime/<instance_id>/`。Pytest 项目本身也从对应仓库副本导入。Matplotlib、Astropy、scikit-learn 的 Windows 环境使用兼容发布版 wheel 中的原生构建文件，同时从基线副本加载 Python 源码。原版与参考补丁仍需离线校准，正式结果以经验证的评测环境为准。

源码准备只使用带校验值的归档和四字段题面。上游内部符号链接在本地派生归档中以对应普通文件物化，原始归档保留。参考补丁、测试补丁和标签在 `control-only/`。解题阶段固定使用 `repo-edit` 工具集以关闭宿主代码执行。独立评测在 Agent 结束后准备单独副本，应用冻结候选补丁和测试补丁，并按冻结标签核对结果。

本轮只完成环境准备、源码导入与入口预检。评测执行使用项目本地适配器，其结果应在原版与参考补丁验证后使用。
