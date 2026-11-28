"""Public Blueprint behavior from Flask issue #5010."""

from flask import Blueprint
import pytest


def test_requirement():
    with pytest.raises(ValueError):
        Blueprint("", __name__)


def test_regression():
    blueprint = Blueprint("catalog", __name__)
    assert blueprint.name == "catalog"
