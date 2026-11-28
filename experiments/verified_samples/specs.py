"""Public identities and dependency pins for four Verified development samples."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    task_id: str
    instance_id: str
    repo: str
    base_commit: str
    dataset_row: int
    issue_url: str
    title: str
    notes: str
    package: str
    source_dir: str
    check_file: str
    requirements: tuple[str, ...]


# The pytest sample imports its runner from the selected source snapshot.
# Other samples install a separate pytest version in their own environments.
PYTEST_REQUIREMENTS = (
    "pytest==8.3.3",
    "iniconfig==2.0.0",
    "packaging==24.1",
    "pluggy==1.5.0",
    "exceptiongroup==1.2.2; python_version < '3.11'",
    "tomli==2.0.1; python_version < '3.11'",
    "colorama==0.4.6; sys_platform == 'win32'",
)


SAMPLES = {
    sample.task_id: sample for sample in (
        Sample(
            task_id="flask-5014",
            instance_id="pallets__flask-5014",
            repo="pallets/flask",
            base_commit="7ee9ceb71e868944a46e1ff00b506772a53a4f1d",
            dataset_row=289,
            issue_url="https://github.com/pallets/flask/issues/5010",
            title="Flask 5014",
            notes="空名称创建 Blueprint 应抛 ValueError，合法名称仍可创建。",
            package="flask",
            source_dir="src",
            check_file="flask_5014.py",
            requirements=PYTEST_REQUIREMENTS + (
                "Werkzeug==2.2.3",
                "Jinja2==3.1.2",
                "MarkupSafe==2.1.5",
                "itsdangerous==2.1.2",
                "click==8.1.7",
                "importlib-metadata==6.6.0; python_version < '3.10'",
                "zipp==3.17.0; python_version < '3.10'",
            ),
        ),
        Sample(
            task_id="sympy-23262",
            instance_id="sympy__sympy-23262",
            repo="sympy/sympy",
            base_commit="fdc707f73a65a429935c01532cd3970d3355eab6",
            dataset_row=489,
            issue_url="https://github.com/sympy/sympy/issues/23224",
            title="SymPy 23262",
            notes="lambdify 保留单元素元组，双元素元组仍正常。",
            package="sympy",
            source_dir=".",
            check_file="sympy_23262.py",
            requirements=PYTEST_REQUIREMENTS + ("mpmath==1.2.1",),
        ),
        Sample(
            task_id="pytest-10051",
            instance_id="pytest-dev__pytest-10051",
            repo="pytest-dev/pytest",
            base_commit="aa55975c7d3f6c9f6d7f68accc41bb7cadf0eb9a",
            dataset_row=330,
            issue_url="https://github.com/pytest-dev/pytest/issues/9877",
            title="pytest 10051",
            notes="caplog 清理后两种查询同步清空，仍能捕获新日志。",
            package="pytest",
            source_dir="src",
            check_file="pytest_10051.py",
            requirements=(
                "attrs==22.2.0",
                "iniconfig==2.0.0",
                "packaging==23.1",
                "pluggy==1.0.0",
                "py==1.11.0",
                "atomicwrites==1.4.1; sys_platform == 'win32'",
                "colorama==0.4.6; sys_platform == 'win32'",
                "tomli==2.0.1; python_version < '3.11'",
            ),
        ),
        Sample(
            task_id="pylint-7080",
            instance_id="pylint-dev__pylint-7080",
            repo="pylint-dev/pylint",
            base_commit="3c5eca2ded3dd2b59ebaf23eb289453b5d2930f0",
            dataset_row=327,
            issue_url="https://github.com/pylint-dev/pylint/issues/6964",
            title="Pylint 7080",
            notes="递归扫描遵守 ignore-paths，也继续检查普通源文件。",
            package="pylint",
            source_dir=".",
            check_file="pylint_7080.py",
            requirements=PYTEST_REQUIREMENTS + (
                "astroid==2.11.7",
                "dill==0.3.6",
                "isort==5.12.0",
                "lazy-object-proxy==1.9.0",
                "mccabe==0.7.0",
                "platformdirs==2.5.2",
                "tomlkit==0.11.8",
                "typing-extensions==4.5.0; python_version < '3.10'",
                "wrapt==1.14.1",
            ),
        ),
    )
}
