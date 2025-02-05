"""
test issue listplans() command
"""

import pandas as pd
import pyRestTable
import pytest
from bluesky import Msg
from bluesky import plans as bp

from ...devices import SimulatedApsPssShutterWithStatus
from .. import listplans


@pytest.fixture(scope="function")
def ns():
    try:
        from IPython import get_ipython

        # ns_dict = get_ipython().user_global_ns
        ns_dict = getattr(get_ipython(), "user_global_ns")
    except (ModuleNotFoundError, AttributeError):
        ns_dict = globals()

    yield ns_dict


def my_generator_function():
    yield 1


class MyClass:
    def a_generator_function_method(self):
        yield 1

    def regular_method(self):
        pass

    def undecorated_plan(self):
        yield Msg("testing")


my_class = MyClass()
shutter = SimulatedApsPssShutterWithStatus(name="shutter")


def test_basic():
    result = listplans()
    assert result is not None
    assert isinstance(result, pyRestTable.Table)
    assert len(result.rows) == 0


@pytest.mark.parametrize(
    "item, exists",
    [
        ["test_globals", True],
        ["my_generator_function", True],
        ["MyClass", True],
        ["my_class", True],
        ["my_class.a_generator_function_method", False],
        ["my_class.regular_method", False],
        ["hollerin_down_the_hall", False],
    ],
)
def test_globals(item, exists, ns):
    assert (item in ns) == exists, f"{item=}  {exists=} {ns=}"


@pytest.mark.parametrize(
    "item, exists",
    [
        ["test_globals", False],
        ["my_generator_function", False],
        ["MyClass", False],
        ["my_class", False],
        ["my_class.a_generator_function_method", False],
        ["my_class.regular_method", False],
        ["a_generator_function_method", True],
        ["regular_method", False],
        ["undecorated_plan", True],
        ["hollerin_down_the_hall", False],
    ],
)
def test_in_class(item, exists):
    result = listplans(my_class)
    assert isinstance(result, pyRestTable.Table)
    generators = [v[0] for v in result.rows]
    assert (item in generators) == exists, f"item={item}  exists={exists} generators={generators}"


@pytest.mark.parametrize(
    "item, plan_name",
    [
        [0, "shutter.get_instantiated_signals"],
        [2, "shutter.walk_signals"],
        [4, "shutter.walk_subdevices"],
    ],
)
def test_shutter(item, plan_name):
    result = listplans(shutter)
    assert result.rows[item][0] == plan_name


@pytest.mark.parametrize(
    "item, plan_name",
    [
        [0, "bluesky.plans.adaptive_scan"],
        [4, "bluesky.plans.inner_product_scan"],
        [34, "bluesky.plans.x2x_scan"],
    ],
)
def test_bluesky_plans(item, plan_name):
    result = listplans(bp)
    assert result.rows[item][0] == plan_name


@pytest.mark.parametrize(
    "library, length",
    [
        [None, 0],
        [bp, 35],
        [pytest, 0],
        [shutter, 5],
        [globals(), 0],
        [my_class, 2],
        ["ns", 0],
    ],
)
def test_number(library, length, ns):
    if library == "ns":
        library = ns
    result = listplans(library)
    assert len(result.rows) == length
