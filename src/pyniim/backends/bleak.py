import asyncio
import logging
import math

from bleak import BleakClient, BleakScanner

from ..errors import ProtocolError
from ..protocol import FrameDecoder, Packet

SERVICE_UUID = "e7810a71-73ae-499d-8c15-faa9aef0c3f2"
CHARACTERISTIC_UUID = "bef8d6c9-9c21-4c9e-b632-bd58c1009f9f"
logger = logging.getLogger(__name__)


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
    def __init__(self, identifier, *, timeout=10.0, write_delay_ms=10.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be positive")
        if not math.isfinite(write_delay_ms) or write_delay_ms < 0:
            raise ValueError("Write delay must be finite and non-negative")
        self.identifier = identifier
        self.timeout = timeout
        self.write_delay_ms = write_delay_ms
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
            logger.debug(
                "BLE connected: write-without-response, max_chunk=%d bytes, write_delay_ms=%g",
                self.characteristic.max_write_without_response_size,
                self.write_delay_ms,
            )
        except BaseException:
            await self.close()
            raise

    def _fail(self, error):
        logger.debug("BLE session failure: %s", error)
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
            logger.debug("RX cmd=0x%02x data=%s", packet.command, packet.data.hex(" "))
            # Errors may arrive during unacknowledged bitmap writes, with no
            # request pending. Persist them so subsequent writes stop as well.
            if packet.command in (0x00, 0xDB):
                name = "unsupported command" if packet.command == 0 else "printer error"
                self._fail(
                    ProtocolError(
                        f"Device reported {name} (0x{packet.command:02x}): {packet.data.hex()}"
                    )
                )
                continue
            if not self.pending or self.pending[1].done():
                logger.debug("Unsolicited RX cmd=0x%02x", packet.command)
                continue
            expected, future = self.pending
            if packet.command in expected:
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
            if self.failure:
                raise self.failure
            await asyncio.wait_for(
                self.client.write_gatt_char(
                    self.characteristic, raw[start : start + size], response=False
                ),
                self.timeout,
            )
            # CoreBluetooth accepting a write is not proof the printer consumed
            # it. Pace every BLE chunk, including the last chunk before PageEnd.
            if self.write_delay_ms:
                await asyncio.sleep(self.write_delay_ms / 1000)
            if self.failure:
                raise self.failure

    async def request(self, command, data=b"\x01", *, response, prefix=b"", timeout=None):
        async with self.lock:
            if self.failure:
                raise self.failure
            expected = (response,) if isinstance(response, int) else tuple(response)
            expected_text = ", ".join(f"0x{code:02x}" for code in expected)
            deadline = self.timeout if timeout is None else timeout
            started = asyncio.get_running_loop().time()
            logger.debug(
                "TX cmd=0x%02x data=%s; waiting for %s (timeout=%gs)",
                command,
                data.hex(" "),
                expected_text,
                deadline,
            )
            future = asyncio.get_running_loop().create_future()
            self.pending = (expected, future)
            try:
                async with asyncio.timeout(deadline):
                    await self.write(prefix + Packet(command, data).encode())
                    reply = await future
                    logger.debug(
                        "Command 0x%02x replied in %.3fs",
                        command,
                        asyncio.get_running_loop().time() - started,
                    )
                    return reply
            except TimeoutError as exc:
                self.failure = ProtocolError("Request timed out; reconnect required")
                message = (
                    f"Timed out after {deadline:g}s sending command 0x{command:02x} "
                    f"or waiting for response {expected_text}; reconnect required"
                )
                logger.debug(message)
                raise TimeoutError(message) from exc
            except asyncio.CancelledError:
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
                    logger.debug("Closing BLE connection")
                    await self.client.disconnect()
            finally:
                self.client = None
                self.characteristic = None
