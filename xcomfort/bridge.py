"""Bridge module for xComfort integration."""
import asyncio
from enum import Enum

import aiohttp

from .comp import Comp, CompState  # noqa: F401
from .connection import SecureBridgeConnection, setup_secure_connection
from .constants import ComponentTypes, DeviceTypes, Messages
from .devices import (
    BridgeDevice,
    DoorSensor,
    Heater,
    Light,
    RcTouch,
    Rocker,
    Shade,
    WindowSensor,
)

# Some HA code relies on bridge having imported these:
from .room import RctMode, RctModeRange, RctState, Room, RoomState  # noqa: F401


class State(Enum):
    """Bridge state enumeration."""

    Uninitialized = 0
    Initializing = 1
    Ready = 2
    Closing = 10


class Bridge:
    """Main bridge class for xComfort communication."""

    def __init__(self, ip_address: str, authkey: str, session=None):
        """Initialize bridge with IP address and auth key."""
        self.ip_address = ip_address
        self.authkey = authkey

        if session is None:
            session = aiohttp.ClientSession()
            closeSession = True
        else:
            closeSession = False

        self._session = session
        self._closeSession = closeSession

        # Values determined from using setpoint slider in app.
        self.rctsetpointallowedvalues = {
            RctMode.Cool: RctModeRange(5.0, 20.0),
            RctMode.Eco: RctModeRange(10.0, 30.0),
            RctMode.Comfort: RctModeRange(18.0, 40.0),
        }
        self._comps = {}
        self._devices = {}
        self._rooms = {}
        self.state = State.Uninitialized
        self.on_initialized = asyncio.Event()
        self.connection = None
        self.connection_subscription = None
        self.logger = lambda x: None

    async def run(self):
        """Run the bridge main loop."""
        if self.state != State.Uninitialized:
            raise RuntimeError("Run can only be called once at a time")

        self.state = State.Initializing

        while self.state != State.Closing:
            try:
                # self.logger(f"Connecting...")
                await self._connect()
                await self.connection.pump()

            except (ConnectionError, RuntimeError) as e:
                self.logger(f"Error: {e!r}")
                await asyncio.sleep(5)

            if self.connection_subscription is not None:
                self.connection_subscription.dispose()

        self.state = State.Uninitialized

    async def switch_device(self, device_id, message):
        """Switch a device on/off."""
        payload = {"deviceId": device_id}
        payload.update(message)
        await self.send_message(Messages.ACTION_SWITCH_DEVICE, payload)

    async def slide_device(self, device_id, message):
        """Slide a device (dimmer/shade)."""
        payload = {"deviceId": device_id}
        payload.update(message)
        await self.send_message(Messages.ACTION_SLIDE_DEVICE, payload)

    async def send_message(self, message_type: Messages, message):
        """Send a message to the bridge."""
        await self.connection.send_message(message_type, message)

    def _add_comp(self, comp):
        """Add a component to the bridge."""
        self._comps[comp.comp_id] = comp

    def _add_device(self, device):
        """Add a device to the bridge."""
        self._devices[device.device_id] = device

    def _add_room(self, room):
        """Add a room to the bridge."""
        self._rooms[room.room_id] = room

    def _handle_SET_DEVICE_STATE(self, payload):
        """Handle device state updates."""
        try:
            device = self._devices[payload["deviceId"]]

            device.handle_state(payload)
        except KeyError:
            return

    def _handle_SET_STATE_INFO(self, payload):
        """Handle state info updates."""
        for item in payload["item"]:
            if "deviceId" in item:
                deviceId = item["deviceId"]
                device = self._devices[deviceId]
                device.handle_state(item)

            elif "roomId" in item:
                roomId = item["roomId"]
                room = self._rooms[roomId]
                room.handle_state(item)

            elif "compId" in item:
                compId = item["compId"]
                comp = self._comps[compId]
                comp.handle_state(item)

            else:
                self.logger(f"Unknown state info: {payload}")

    def _create_comp_from_payload(self, payload):
        """Create a component from payload data."""
        comp_id = payload["compId"]
        name = payload["name"]
        comp_type = payload["compType"]

        return Comp(self, comp_id, comp_type, name, payload)

    def _create_device_from_payload(self, payload):
        """Create a device from payload data."""
        device_id = payload["deviceId"]
        name = payload["name"]
        dev_type = payload["devType"]
        comp_id = payload["compId"]
        if dev_type in (DeviceTypes.ACTUATOR_SWITCH, DeviceTypes.ACTUATOR_DIMM):
            if payload.get("usage") == 0:
                # If usage = 1 then it's configured as a "load",
                # and not as a light.
                dimmable = payload["dimmable"]
                return Light(self, device_id, name, dimmable)

        elif dev_type == DeviceTypes.SHADING_ACTUATOR:
            return Shade(self, device_id, name, comp_id, payload)

        elif dev_type == DeviceTypes.HEATING_ACTUATOR:
            return Heater(self, device_id, name, comp_id)

        elif dev_type == DeviceTypes.RC_TOUCH:
            return RcTouch(self, device_id, name, comp_id)

        elif dev_type == DeviceTypes.SWITCH:
            component: Comp | None = self._comps.get(comp_id)
            if component and component.comp_type == ComponentTypes.DOOR_WINDOW_SENSOR:
                if component.payload.get("mode") == "1310":
                    return DoorSensor(self, device_id, name, comp_id, payload)
                return WindowSensor(self, device_id, name, comp_id, payload)

        elif dev_type == DeviceTypes.ROCKER:
            # What Xcomfort calls a rocker HomeAssistant (and most humans) call a
            # switch
            return Rocker(self, device_id, name, comp_id, payload)

        return BridgeDevice(self, device_id, name)

    def _create_room_from_payload(self, payload):
        """Create a room from payload data."""
        room_id = payload["roomId"]
        name = payload["name"]

        return Room(self, room_id, name)

    def _handle_comp_payload(self, payload):
        """Handle component payload."""
        comp_id = payload["compId"]

        comp = self._comps.get(comp_id)

        if comp is None:
            comp = self._create_comp_from_payload(payload)

            if comp is None:
                return

            self._add_comp(comp)

        comp.handle_state(payload)

    def _handle_device_payload(self, payload):
        """Handle device payload."""
        device_id = payload["deviceId"]

        device = self._devices.get(device_id)

        if device is None:
            device = self._create_device_from_payload(payload)

            if device is None:
                return

            self._add_device(device)

        device.handle_state(payload)

    def _handle_room_payload(self, payload):
        """Handle room payload."""
        room_id = payload["roomId"]

        room = self._rooms.get(room_id)

        if room is None:
            room = self._create_room_from_payload(payload)

            if room is None:
                return

            self._add_room(room)

        room.handle_state(payload)

    def _handle_SET_ALL_DATA(self, payload):
        """Handle initial data setup."""
        if "lastItem" in payload:
            self.state = State.Ready
            self.on_initialized.set()

        if "devices" in payload:
            for device_payload in payload["devices"]:
                try:
                    self._handle_device_payload(device_payload)
                except (KeyError, ValueError) as e:
                    self.logger(f"Failed to handle device payload: {e!s}")

        if "comps" in payload:
            for comp_payload in payload["comps"]:
                try:
                    self._handle_comp_payload(comp_payload)
                except (KeyError, ValueError) as e:
                    self.logger(f"Failed to handle comp payload: {e!s}")

        if "rooms" in payload:
            for room_payload in payload["rooms"]:
                try:
                    self._handle_room_payload(room_payload)
                except (KeyError, ValueError) as e:
                    self.logger(f"Failed to handle room payload: {e!s}")

        if "roomHeating" in payload:
            for room_payload in payload["roomHeating"]:
                try:
                    self._handle_room_payload(room_payload)
                except (KeyError, ValueError) as e:
                    self.logger(f"Failed to handle room payload: {e!s}")

    def _handle_UNKNOWN(self, message_type, payload):
        """Handle unknown message types."""
        self.logger(f"Unhandled package [{message_type.name}]: {payload}")

    def _onMessage(self, message):
        """Handle incoming messages."""
        if "payload" in message:
            message_type = Messages(message["type_int"])
            method_name = "_handle_" + message_type.name

            method = getattr(self, method_name, lambda p: self._handle_UNKNOWN(message_type, p))
            try:
                method(message["payload"])
            except (KeyError, ValueError) as e:
                self.logger(f"Unknown error with: {method_name}: {e!s}")
        else:
            self.logger(f"Not known: {message}")

    async def _connect(self):
        """Establish connection to bridge."""
        self.connection = await setup_secure_connection(self._session, self.ip_address, self.authkey)
        self.connection_subscription = self.connection.messages.subscribe(self._onMessage)

    async def close(self):
        """Close the bridge connection."""
        self.state = State.Closing
        self.on_initialized.clear()

        if isinstance(self.connection, SecureBridgeConnection):
            self.connection_subscription.dispose()
            await self.connection.close()

        if self._closeSession:
            await self._session.close()

    async def wait_for_initialization(self):
        """Wait for bridge initialization to complete."""
        return await self.on_initialized.wait()

    async def get_comps(self):
        """Get all components."""
        await self.wait_for_initialization()

        return self._comps

    async def get_devices(self):
        """Get all devices."""
        await self.wait_for_initialization()

        return self._devices

    async def get_rooms(self):
        """Get all rooms."""
        await self.wait_for_initialization()

        return self._rooms
