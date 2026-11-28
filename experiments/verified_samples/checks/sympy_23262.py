"""Public lambdify behavior from SymPy issue #23224."""

from sympy import lambdify


def test_requirement():
    generate_value = lambdify([], (1,), modules="math")
    result = generate_value()
    assert isinstance(result, tuple)
    assert result == (1,)


def test_regression():
    generate_value = lambdify([], (1, 2), modules="math")
    result = generate_value()
    assert isinstance(result, tuple)
    assert result == (1, 2)
