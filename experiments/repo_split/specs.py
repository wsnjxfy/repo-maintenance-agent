"""Trusted runtime settings for the local 15-repository split."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re


PACK_NAME = "repo-split-15-20261128"
TOOLSET = "repo-edit"


@dataclass(frozen=True)
class RepoSpec:
    task_id: str
    instance_id: str
    group: str
    repo: str
    base_commit: str
    source_dir: str
    package: str
    f2p_count: int
    p2p_count: int
    native: bool = False


# Prefix IDs to preserve the existing four Web entries, including Flask 5014.
_RUNTIME = {
    "pallets__flask-5014": ("flask-5014", "src", "flask"),
    "psf__requests-2931": ("requests-2931", ".", "requests"),
    "pytest-dev__pytest-5631": ("pytest-5631", "src", "pytest"),
    "pylint-dev__pylint-8898": ("pylint-8898", ".", "pylint"),
    "sphinx-doc__sphinx-10673": ("sphinx-10673", ".", "sphinx"),
    "sympy__sympy-20438": ("sympy-20438", ".", "sympy"),
    "django__django-12155": ("django-12155", ".", "django"),
    "scikit-learn__scikit-learn-25102": ("scikit-learn-25102", ".", "sklearn"),
    "pdm-project__pdm-3250": ("pdm-3250", "src", "pdm"),
    "dynaconf__dynaconf-1241": ("dynaconf-1241", ".", "dynaconf"),
    "astropy__astropy-13033": ("astropy-13033", ".", "astropy"),
    "matplotlib__matplotlib-20859": ("matplotlib-20859", "lib", "matplotlib"),
    "pydata__xarray-6938": ("xarray-6938", ".", "xarray"),
    "mwaskom__seaborn-3187": ("seaborn-3187", ".", "seaborn"),
    "jazzband__tablib-613": ("tablib-613", "src", "tablib"),
}
_NATIVE = {"astropy", "matplotlib", "sklearn"}


def load_catalog():
    return json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))


def load_specs():
    rows = load_catalog()["instances"]
    if len(rows) != len(_RUNTIME) or {row["instance_id"] for row in rows} != set(_RUNTIME):
        raise ValueError("The local repository split roster is incomplete")
    if Counter(row["split"] for row in rows) != {"dev": 10, "test": 5}:
        raise ValueError("The local split must contain 10 development and 5 test repos")
    if len({row["repo"].casefold() for row in rows}) != len(rows):
        raise ValueError("The local split repeats a repository")
    specs = []
    for row in rows:
        short, source_dir, package = _RUNTIME[row["instance_id"]]
        if not re.fullmatch(r"[0-9a-f]{40}", row["base_commit"]):
            raise ValueError("The local split requires pinned Git commits")
        specs.append(RepoSpec(f"{row['split']}-{short}", row["instance_id"], row["split"],
                              row["repo"], row["base_commit"], source_dir, package,
                              row["f2p_count"], row["p2p_count"], package in _NATIVE))
    return specs
