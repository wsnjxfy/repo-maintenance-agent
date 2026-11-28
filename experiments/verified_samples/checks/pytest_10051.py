"""Public log capture behavior from pytest issue #9877."""

import logging


LOGGER = logging.getLogger(__name__)


def captured_messages(records):
    return [record.getMessage() for record in records if record.name == LOGGER.name]


def test_requirement(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    LOGGER.warning("before clear")
    caplog.clear()
    assert captured_messages(caplog.records) == []
    assert captured_messages(caplog.get_records("call")) == []

    LOGGER.warning("after clear")
    assert captured_messages(caplog.records) == ["after clear"]
    assert captured_messages(caplog.get_records("call")) == ["after clear"]


def test_regression(caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER.name)
    LOGGER.warning("without clear")
    assert captured_messages(caplog.records) == ["without clear"]
    assert captured_messages(caplog.get_records("call")) == ["without clear"]
