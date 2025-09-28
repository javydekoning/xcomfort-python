"""Connection module for xComfort integration."""

from base64 import b64decode, b64encode
from enum import IntEnum
import json
import secrets
import string

import aiohttp
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes
import rx
import rx.operators as ops

from .constants import Messages


class ConnectionState(IntEnum):
    """Connection state enumeration."""

    Initial = 1
    Loading = 2
    Loaded = 3


def generateSalt():
    """Generate a random salt string."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for i in range(12))


def hash_password(deviceId, authKey, salt):
    """Hash password for authentication."""
    hasher = SHA256.new()
    hasher.update(deviceId)
    hasher.update(authKey)
    inner = hasher.hexdigest().encode()
    hasher = SHA256.new()
    hasher.update(salt)
    hasher.update(inner)

    return hasher.hexdigest()


def _pad_string(value):
    """Pad string to AES block size."""
    length = len(value)
    pad_size = AES.block_size - (length % AES.block_size)
    return value.ljust(length + pad_size, b"\x00")


def _raise_connection_error(msg):
    """Raise connection error."""
    raise ConnectionError(msg)


def _raise_login_error(msg):
    """Raise login error."""
    raise ConnectionError(msg)


def _raise_secure_connection_error(msg):
    """Raise secure connection error."""
    raise ConnectionError(msg)


def _raise_token_error(msg):
    """Raise token error."""
    raise ConnectionError(msg)


def _raise_renew_token_error(msg):
    """Raise renew token error."""
    raise ConnectionError(msg)


async def setup_secure_connection(session, ip_address, authkey):
    """Set up secure connection to xComfort bridge."""

    async def __receive(ws):
        """Receive message from websocket."""
        msg = await ws.receive()
        msg = msg.data[:-1]
        return json.loads(msg)

    async def __send(ws, data):
        """Send message to websocket."""
        msg = json.dumps(data)
        await ws.send_str(msg)

    ws = await session.ws_connect(f"http://{ip_address}/")

    try:
        msg = await __receive(ws)

        # {'type_int': 0, 'ref': -1, 'info': 'no client-connection available (all used)!'}
        if msg["type_int"] == Messages.NACK:
            _raise_connection_error(msg["info"])

        deviceId = msg["payload"]["device_id"]
        connectionId = msg["payload"]["connection_id"]

        await __send(
            ws,
            {
                "type_int": 11,
                "mc": -1,
                "payload": {
                    "client_type": "shl-app",
                    "client_id": "c956e43f999f8004",
                    "client_version": "3.0.0",
                    "connection_id": connectionId,
                },
            },
        )

        msg = await __receive(ws)

        if msg["type_int"] == Messages.CONNECTION_DECLINED:
            _raise_connection_error(msg["payload"]["error_message"])

        await __send(ws, {"type_int": 14, "mc": -1})

        msg = await __receive(ws)
        publicKey = msg["payload"]["public_key"]

        rsa = RSA.import_key(publicKey)

        key = get_random_bytes(32)
        iv = get_random_bytes(16)

        cipher = PKCS1_v1_5.new(rsa)
        secret = b64encode(cipher.encrypt((key.hex() + ":::" + iv.hex()).encode()))
        secret = secret.decode()

        await __send(ws, {"type_int": 16, "mc": -1, "payload": {"secret": secret}})

        connection = SecureBridgeConnection(ws, key, iv, deviceId)

        # Start LOGIN

        msg = await connection.receive()

        if msg["type_int"] != 17:
            _raise_secure_connection_error("Failed to establish secure connection")

        salt = generateSalt()
        password = hash_password(deviceId.encode(), authkey.encode(), salt.encode())

        await connection.send_message(
            30, {"username": "default", "password": password, "salt": salt}
        )

        msg = await connection.receive()

        if msg["type_int"] != 32:
            _raise_login_error("Login failed")

        token = msg["payload"]["token"]
        await connection.send_message(33, {"token": token})

        # {"type_int":34,"mc":-1,"payload":{"valid":true,"remaining":8640000}}
        msg = await connection.receive()

        # Renew token
        await connection.send_message(37, {"token": token})

        msg = await connection.receive()

        if msg["type_int"] != 38:
            _raise_renew_token_error("Login failed")

        token = msg["payload"]["token"]

        await connection.send_message(33, {"token": token})

        # {"type_int":34,"mc":-1,"payload":{"valid":true,"remaining":8640000}}
        msg = await connection.receive()

    except Exception:
        await ws.close()
        raise

    else:
        return connection

class SecureBridgeConnection:
    """Secure connection to xComfort bridge."""

    def __init__(self, websocket, key, iv, device_id):
        """Initialize secure connection."""
        self.websocket = websocket
        self.key = key
        self.iv = iv
        self.device_id = device_id

        self.state = ConnectionState.Initial
        self._messageSubject = rx.subject.Subject()
        self.mc = 0

        self.messages = self._messageSubject.pipe(ops.as_observable())

    def __cipher(self):
        """Get AES cipher for encryption/decryption."""
        return AES.new(self.key, AES.MODE_CBC, self.iv)

    def __decrypt(self, data):
        """Decrypt received data."""
        ct = b64decode(data)
        data = self.__cipher().decrypt(ct)
        data = data.rstrip(b"\x00")

        if not data:
            return {}

        return json.loads(data.decode())

    async def pump(self):
        """Pump messages from the connection."""
        self.state = ConnectionState.Loading

        await self.send_message(240, {})
        await self.send_message(242, {})
        await self.send_message(2, {})

        async for msg in self.websocket:
            if msg.type == aiohttp.WSMsgType.TEXT:
                result = self.__decrypt(msg.data)

                if "mc" in result:
                    # ACK
                    await self.send({"type_int": 1, "ref": result["mc"]})

                if "payload" in result:
                    self._messageSubject.on_next(result)

            elif msg.type == aiohttp.WSMsgType.ERROR:
                break

    async def close(self):
        """Close the connection."""
        await self.websocket.close()

    async def receive(self):
        """Receive a message from the connection."""
        msg = await self.websocket.receive()

        return self.__decrypt(msg.data)

    async def send_message(self, message_type, payload):
        """Send a message through the connection."""
        self.mc += 1

        if isinstance(message_type, Messages):
            message_type = message_type.value

        await self.send({"type_int": message_type, "mc": self.mc, "payload": payload})

    async def send(self, data):
        """Send data through the connection."""
        msg = json.dumps(data)
        msg = _pad_string(msg.encode())
        msg = self.__cipher().encrypt(msg)
        msg = b64encode(msg).decode() + "\u0004"
        await self.websocket.send_str(msg)
