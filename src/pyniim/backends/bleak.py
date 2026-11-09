import asyncio

from bleak import BleakClient, BleakScanner

from ..errors import ProtocolError
from ..protocol import FrameDecoder, Packet

SERVICE_UUID = "e7810a71-73ae-499d-8c15-faa9aef0c3f2"
CHARACTERISTIC_UUID = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"


def device_address(identifier):
    address = identifier.removeprefix("ble://")
    if not address or "://" in address:
        raise ValueError("Expected a BLE device identifier or ble://<device-id>")
    return address


async def discover_devices(timeout=5.0):
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    # Also retain unrecognized advertisements: names and service advertising vary by firmware.
    return [
        dict(
            identifier=f"ble://{device.address}",
            name=advertisement.local_name or device.name,
            rssi=advertisement.rssi,
            service_match=SERVICE_UUID in [s.lower() for s in advertisement.service_uuids],
        )
        for device, advertisement in found.values()
    ]


class BleakTransport:
    def __init__(self, identifier, *, timeout=10.0):
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        self.identifier = identifier
        self.timeout = timeout
        self.client = None
        self.characteristic = None
        self.decoder = FrameDecoder()
        self.pending = None
        self.lock = asyncio.Lock()
        self.failure = None

    async def open(self):
        device = await BleakScanner.find_device_by_address(
            device_address(self.identifier), timeout=self.timeout
        )
        if device is None:
            raise ProtocolError(f"BLE device not found: {self.identifier}")
        self.client = BleakClient(
            device, disconnected_callback=self._disconnected, timeout=self.timeout
        )
        try:
            await self.client.connect()
            service = self.client.services.get_service(SERVICE_UUID)
            self.characteristic = (
                service.get_characteristic(CHARACTERISTIC_UUID) if service else None
            )
            if self.characteristic is None:
                raise ProtocolError("NIIMBOT GATT service/characteristic not found")
            properties = self.characteristic.properties
            if "notify" not in properties or "write-without-response" not in properties:
                raise ProtocolError("Required notify/write-without-response properties missing")
            await self.client.start_notify(self.characteristic, self._notify)
        except BaseException:
            await self.close()
            raise

    def _fail(self, error):
        self.failure = error
        if self.pending and not self.pending[1].done():
            self.pending[1].set_exception(error)

    def _disconnected(self, client):
        self._fail(ProtocolError("BLE disconnected; reconnect before another operation"))

    def _notify(self, characteristic, data):
        try:
            packets = self.decoder.feed(data)
        except ProtocolError as exc:
            self._fail(exc)
            return
        for packet in packets:
            if not self.pending or self.pending[1].done():
                continue
            expected, future = self.pending
            if packet.command == 0:
                self._fail(ProtocolError(f"Device rejected command: {packet.data.hex()}"))
            elif packet.command in expected:
                future.set_result(packet)

    async def write(self, raw):
        if self.failure:
            raise self.failure
        if self.client is None or not self.client.is_connected:
            raise ProtocolError("BLE transport is not connected")
        size = self.characteristic.max_write_without_response_size
        if size < 1:
            raise ProtocolError("Invalid BLE write capacity")
        for start in range(0, len(raw), size):
            await asyncio.wait_for(
                self.client.write_gatt_char(
                    self.characteristic, raw[start : start + size], response=False
                ),
                self.timeout,
            )

    async def request(self, command, data=b"\x01", *, response, prefix=b"", timeout=None):
        async with self.lock:
            if self.failure:
                raise self.failure
            expected = (response,) if isinstance(response, int) else tuple(response)
            future = asyncio.get_running_loop().create_future()
            self.pending = (expected, future)
            try:
                async with asyncio.timeout(self.timeout if timeout is None else timeout):
                    await self.write(prefix + Packet(command, data).encode())
                    return await future
            except (TimeoutError, asyncio.CancelledError):
                # No sequence IDs: a late reply must never satisfy a subsequent request.
                self.failure = ProtocolError("Request interrupted; reconnect required")
                raise
            finally:
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()  # Consume an error if the write failed concurrently.
                self.pending = None

    async def close(self):
        if self.client is not None:
            try:
                if self.client.is_connected:
                    await self.client.disconnect()
            finally:
                self.client = None
                self.characteristic = None
