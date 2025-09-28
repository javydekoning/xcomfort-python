"""Devices module for xComfort integration."""

import rx

from .constants import Messages, ShadeOperationState


class DeviceState:
    """Base device state class."""

    def __init__(self, payload):
        """Initialize device state with payload."""
        self.raw = payload

    def __str__(self):
        """String representation of device state."""
        return f"DeviceState({self.raw})"


class LightState(DeviceState):
    """Light device state."""

    def __init__(self, switch, dimmvalue, payload):
        """Initialize light state."""
        DeviceState.__init__(self, payload)
        self.switch = switch
        self.dimmvalue = dimmvalue

    def __str__(self):
        """String representation of light state."""
        return f"LightState({self.switch}, {self.dimmvalue})"

    __repr__ = __str__


class RcTouchState(DeviceState):
    """RcTouch device state."""

    def __init__(self, temperature, humidity, payload):
        """Initialize RcTouch state."""
        DeviceState.__init__(self, payload)
        self.temperature = temperature
        self.humidity = humidity

    def __str__(self):
        """String representation of RcTouch state."""
        return f"RcTouchState({self.temperature}, {self.humidity})"

    __repr__ = __str__


class HeaterState(DeviceState):
    """Heater device state."""

    def __init__(self, payload):
        """Initialize heater state."""
        DeviceState.__init__(self, payload)

    def __str__(self):
        """String representation of heater state."""
        return f"HeaterState({self.payload})"

    __repr__ = __str__


class ShadeState(DeviceState):
    """Shade device state."""

    def __init__(self):
        """Initialize shade state."""
        self.raw = {}
        self.current_state: int | None = None
        self.is_safety_enabled: bool | None = None
        self.position: int | None = None

    def update_from_partial_state_update(self, payload: dict) -> None:
        """Update state from partial state update."""
        self.raw.update(payload)

        if (current_state := payload.get("curstate")) is not None:
            self.current_state = current_state

        if (safety := payload.get("shSafety")) is not None:
            self.is_safety_enabled = safety != 0

        if (position := payload.get("shPos")) is not None:
            self.position = position

    @property
    def is_closed(self) -> bool | None:
        """Check if shade is closed."""
        if (self.position is None) or (0 < self.position < 100):
            # It's not fully closed or open and can move both ways, or we don't know
            return None

        # It's fully extended, i.e. "closed"
        return self.position == 100

    def __str__(self) -> str:
        """String representation of shade state."""
        return f"ShadeState(current_state={self.current_state} is_safety_enabled={self.is_safety_enabled} position={self.position} raw={self.raw})"


class BridgeDevice:
    """Base bridge device class."""

    def __init__(self, bridge, device_id, name):
        """Initialize bridge device."""
        self.bridge = bridge
        self.device_id = device_id
        self.name = name

        self.state = rx.subject.BehaviorSubject(None)

    def handle_state(self, payload):
        """Handle state updates."""
        self.state.on_next(DeviceState(payload))


class Light(BridgeDevice):
    """Light device class."""

    def __init__(self, bridge, device_id, name, dimmable):
        """Initialize light device."""
        BridgeDevice.__init__(self, bridge, device_id, name)

        self.dimmable = dimmable

    def interpret_dimmvalue_from_payload(self, switch, payload):
        """Interpret dimmvalue from payload."""
        if not self.dimmable:
            return 99

        if not switch:
            return self.state.value.dimmvalue if self.state.value is not None else 99

        return payload["dimmvalue"]

    def handle_state(self, payload):
        """Handle light state updates."""
        switch = payload["switch"]
        dimmvalue = self.interpret_dimmvalue_from_payload(switch, payload)

        self.state.on_next(LightState(switch, dimmvalue, payload))

    async def switch(self, switch: bool):
        """Switch light on/off."""
        await self.bridge.switch_device(self.device_id, {"switch": switch})

    async def dimm(self, value: int):
        """Set dimming value."""
        value = max(0, min(99, value))
        await self.bridge.slide_device(self.device_id, {"dimmvalue": value})

    def __str__(self):
        """String representation of light device."""
        return f'Light({self.device_id}, "{self.name}", dimmable: {self.dimmable}, state:{self.state.value})'

    __repr__ = __str__


class RcTouch(BridgeDevice):
    """RcTouch device class."""

    def __init__(self, bridge, device_id, name, comp_id):
        """Initialize RcTouch device."""
        BridgeDevice.__init__(self, bridge, device_id, name)

        self.comp_id = comp_id

    def handle_state(self, payload):
        """Handle RcTouch state updates."""
        temperature = None
        humidity = None
        if "info" in payload:
            for info in payload["info"]:
                if info["text"] == "1222":
                    temperature = float(info["value"])
                if info["text"] == "1223":
                    humidity = float(info["value"])

        if temperature is not None and humidity is not None:
            self.state.on_next(RcTouchState(temperature, humidity, payload))


class Heater(BridgeDevice):
    """Heater device class."""

    def __init__(self, bridge, device_id, name, comp_id):
        """Initialize heater device."""
        BridgeDevice.__init__(self, bridge, device_id, name)

        self.comp_id = comp_id


class Shade(BridgeDevice):
    """Shade device class."""

    def __init__(self, bridge, device_id, name, comp_id, payload):
        """Initialize shade device."""
        BridgeDevice.__init__(self, bridge, device_id, name)

        self.component = bridge._comps.get(comp_id)  # noqa: SLF001
        self.payload = payload

        # We get partial updates of shade state across different state updates, so
        # we aggregate them via this object
        self.__shade_state = ShadeState()

        self.comp_id = comp_id

    @property
    def supports_go_to(self) -> bool | None:
        """Check if shade supports go to position."""
        # "go to" is whether a specific position can be set, i.e. 50 meaning halfway down
        # Not all actuators support this, even if they can be stopped at arbitrary positions.
        if (component := self.bridge._comps.get(self.comp_id)) is not None:  # noqa: SLF001
            return component.comp_type == 86 and self.payload.get("shRuntime") == 1
        return None

    def handle_state(self, payload):
        """Handle shade state updates."""
        self.__shade_state.update_from_partial_state_update(payload)
        self.state.on_next(self.__shade_state)

    async def send_state(self, state, **kw):
        """Send shade state to bridge."""
        if self.__shade_state.is_safety_enabled:
            # Do not trigger changes if safety is on. The official xcomfort client does
            # this check in the client, so we do that too just to be safe.
            return

        await self.bridge.send_message(
            Messages.SET_DEVICE_SHADING_STATE,
            {"deviceId": self.device_id, "state": state, **kw},
        )

    async def move_down(self):
        """Move shade down."""
        await self.send_state(ShadeOperationState.CLOSE)

    async def move_up(self):
        """Move shade up."""
        await self.send_state(ShadeOperationState.OPEN)

    async def move_stop(self):
        """Stop shade movement."""
        if self.__shade_state.is_safety_enabled:
            return

        await self.send_state(ShadeOperationState.STOP)

    async def move_to_position(self, position: int):
        """Move shade to specific position."""
        assert self.supports_go_to and 0 <= position <= 100
        await self.send_state(ShadeOperationState.GO_TO, value=position)

    def __str__(self) -> str:
        """String representation of shade device."""
        return f"<Shade device_id={self.device_id} name={self.name} state={self.state} supports_go_to={self.supports_go_to}>"


class DoorWindowSensor(BridgeDevice):
    """Door/window sensor device class."""

    def __init__(self, bridge, device_id, name, comp_id, payload):
        """Initialize door/window sensor device."""
        BridgeDevice.__init__(self, bridge, device_id, name)

        self.comp_id = comp_id
        self.payload = payload
        self.is_open: bool | None = None
        self.is_closed: bool | None = None

    def handle_state(self, payload):
        """Handle door/window sensor state updates."""
        if (state := payload.get("curstate")) is not None:
            self.is_closed = state == 1
            self.is_open = not self.is_closed

        self.state.on_next(self.is_closed)


class WindowSensor(DoorWindowSensor):
    """Window sensor device class."""



class DoorSensor(DoorWindowSensor):
    """Door sensor device class."""



class Rocker(BridgeDevice):
    """Rocker device class."""

    def __init__(self, bridge, device_id, name, comp_id, payload):
        """Initialize rocker device."""
        BridgeDevice.__init__(self, bridge, device_id, name)
        self.comp_id = comp_id
        self.payload = payload
        self.is_on: bool | None = None
        if "curstate" in payload:
            self.is_on = bool(payload["curstate"])

    @property
    def name_with_controlled(self) -> str:
        """Name of Rocker, with the names of controlled devices in parens."""
        names_of_controlled: set[str] = set()
        for device_id in self.payload.get("controlId", []):
            device = self.bridge._devices.get(device_id)  # noqa: SLF001
            if device:
                names_of_controlled.add(device.name)

        return f"{self.name} ({', '.join(sorted(names_of_controlled))})"

    def handle_state(self, payload, broadcast: bool = True) -> None:
        """Handle rocker state updates."""
        self.payload.update(payload)
        self.is_on = bool(payload["curstate"])
        if broadcast:
            self.state.on_next(self.is_on)

    def __str__(self):
        """String representation of rocker device."""
        return f'Rocker({self.device_id}, "{self.name}", is_on: {self.is_on} payload: {self.payload})'
