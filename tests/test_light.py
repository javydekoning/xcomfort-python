"""Test module for Light device."""
import pytest

from xcomfort.bridge import Bridge
from xcomfort.devices import Light


class MockBridge(Bridge):
    """Mock bridge for testing."""

    def __init__(self):
        """Initialize mock bridge."""
        self._sent_message = None

    async def send_message(self, message_type, message):
        """Mock send message method."""
        self._sent_message = message


@pytest.mark.asyncio
async def test_light_switch_on():
    """Test switching light on."""
    bridge = MockBridge()
    device = Light(bridge, 1, "", True)

    await device.switch(True)

    assert bridge._sent_message == {"deviceId": 1, "switch": True}  # noqa: SLF001


def test_lightstate_switch_on():
    """Test light state when switching on."""
    device = Light(None, 1, "", True)

    payload = {"switch": True, "dimmvalue": 50}

    device.handle_state(payload)

    assert device.state.value.switch is True
    assert device.state.value.dimmvalue == 50


def test_lightstate_switch_on_when_not_dimmable():
    """Test light state when switching on non-dimmable light."""
    device = Light(None, 1, "", False)

    payload = {"switch": True}

    device.handle_state(payload)

    assert device.state.value.switch is True
