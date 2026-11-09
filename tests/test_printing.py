import asyncio

import pytest
from PIL import Image

from pyniim import AsyncPrinter, Printer
from pyniim.backends.bleak import BleakTransport
from pyniim.backends.helpers import send_async
from pyniim.conversion import convert
from pyniim.errors import PrintError, ProtocolError
from pyniim.protocol import FrameDecoder, Packet
from pyniim.raster import NiimbotRaster


class FakeTransport:
    def __init__(self, model_id=4608, fail_command=None, stalled=False):
        self.model_id = model_id
        self.fail_command = fail_command
        self.stalled = stalled
        self.commands = []
        self.opened = False
        self.closed = False
        self.pages = 0
        self.copies = 1

    async def open(self):
        self.opened = True

    async def close(self):
        self.closed = True

    async def request(self, command, data=b"\x01", *, response, **options):
        self.commands.append((command, data))
        if command == self.fail_command:
            raise TimeoutError("injected timeout")
        if command == 0x40 and data == b"\x08":
            payload = self.model_id.to_bytes(2, "big")
        elif command == 0x01:
            self.pages = 0
            payload = b"\x01"
        elif command == 0xA5:
            payload = bytes(13)
        elif command == 0x13:
            self.copies = int.from_bytes(data[4:6], "big")
            payload = b"\x01"
        elif command == 0xE3:
            if not self.stalled:
                self.pages += self.copies
            payload = b"\x01"
        elif command == 0xA3:
            payload = self.pages.to_bytes(2, "big") + b"\x64\x64"
        else:
            payload = b"\x01"
        return Packet(response if isinstance(response, int) else response[0], payload)

    async def write(self, raw):
        self.commands.extend((packet.command, packet.data) for packet in FrameDecoder().feed(raw))


def make_job(copies=1, pages=1):
    return convert(
        NiimbotRaster(), [Image.new("RGB", (12, 12), "black")] * pages, "1x1", copies=copies
    )


async def test_handshake_and_two_page_copies():
    transport = FakeTransport()
    async with AsyncPrinter("test", transport=transport) as printer:
        result = await printer.send(make_job(copies=2, pages=2))
        assert result["outcome"] == "printed"
        assert result["pages_printed"] == 4
        assert result["ready_for_next_job"]
    commands = [cmd for cmd, _ in transport.commands]
    assert commands[:3] == [0xC1, 0xA5, 0x40]
    assert (0x01, b"\x00\x04" + bytes(5)) in transport.commands
    assert commands.count(0xE3) == 2
    assert commands[-2:] == [0xA3, 0xF3]
    assert transport.closed


async def test_model_mismatch_never_starts_print():
    transport = FakeTransport(model_id=4096)
    with pytest.raises(ProtocolError, match="Expected M2_H"):
        async with AsyncPrinter("test", transport=transport):
            pass
    assert transport.closed
    assert not any(cmd == 0x01 for cmd, _ in transport.commands)


@pytest.mark.parametrize("command", [0x01, 0xE3, 0xA3, 0xF3])
async def test_timeout_does_not_retry_or_end_early(command):
    transport = FakeTransport(fail_command=command)
    async with AsyncPrinter("test", transport=transport) as printer:
        with pytest.raises(PrintError) as exc:
            await printer.send(make_job())
        assert exc.value.result["outcome"] == "unknown"
        assert not exc.value.result["ready_for_next_job"]
        with pytest.raises(ProtocolError):
            await printer.send(make_job())
    commands = [cmd for cmd, _ in transport.commands]
    assert commands.count(command) == 1
    assert commands.count(0xF3) == (1 if command == 0xF3 else 0)


async def test_stall_deadline_and_cancellation():
    transport = FakeTransport(stalled=True)
    async with AsyncPrinter("test", timeout=0.01, transport=transport) as printer:
        with pytest.raises(PrintError):
            await printer.send(make_job())
    assert not any(cmd == 0xF3 for cmd, _ in transport.commands)
    assert transport.closed


async def test_nonblocking_rejected_before_connection():
    with pytest.raises(ValueError, match="blocking=False"):
        await send_async(make_job(), "test", blocking=False)


def test_sync_session_keeps_one_loop():
    class LoopTransport(FakeTransport):
        async def open(self):
            self.loop = asyncio.get_running_loop()
            await super().open()

        async def request(self, *args, **kwargs):
            assert self.loop is asyncio.get_running_loop()
            return await super().request(*args, **kwargs)

    transport = LoopTransport()
    with Printer("test", transport=transport) as printer:
        assert printer.send(make_job())["did_print"]
        assert printer.status()["model_id"] == 4608
    assert transport.closed


async def test_sync_api_rejects_running_event_loop():
    with pytest.raises(RuntimeError, match="event loop"):
        with Printer("test", transport=FakeTransport()):
            pass


class FakeCharacteristic:
    max_write_without_response_size = 5


class FakeClient:
    is_connected = True

    def __init__(self, transport, respond=True):
        self.transport = transport
        self.respond = respond
        self.writes = []
        self.decoder = FrameDecoder()

    async def write_gatt_char(self, characteristic, raw, response):
        assert response is False
        assert len(raw) <= 5
        self.writes.append(raw)
        for packet in self.decoder.feed(raw):
            if self.respond:
                encoded = Packet(0x31, b"\x01").encode()
                self.transport._notify(characteristic, encoded[:3])
                self.transport._notify(characteristic, encoded[3:])


async def test_transport_chunks_and_handles_immediate_fragmented_response():
    transport = BleakTransport("test")
    transport.characteristic = FakeCharacteristic()
    transport.client = FakeClient(transport)
    result = await transport.request(0x21, response=0x31)
    assert result.data == b"\x01"
    assert b"".join(transport.client.writes) == Packet(0x21, b"\x01").encode()


async def test_late_response_cannot_satisfy_new_request():
    transport = BleakTransport("test", timeout=0.001)
    transport.characteristic = FakeCharacteristic()
    transport.client = FakeClient(transport, respond=False)
    with pytest.raises(TimeoutError):
        await transport.request(0x21, response=0x31)
    transport._notify(None, Packet(0x31, b"\x01").encode())
    with pytest.raises(ProtocolError, match="reconnect"):
        await transport.request(0x21, response=0x31)


async def test_disconnect_wakes_pending_request():
    transport = BleakTransport("test")
    transport.characteristic = FakeCharacteristic()
    transport.client = FakeClient(transport, respond=False)
    task = asyncio.create_task(transport.request(0x21, response=0x31))
    await asyncio.sleep(0)
    transport._disconnected(transport.client)
    with pytest.raises(ProtocolError, match="disconnected"):
        await task


async def test_cancelled_job_closes_without_blind_print_end():
    transport = FakeTransport(stalled=True)
    polling = asyncio.Event()
    original = transport.request

    async def request(command, *args, **kwargs):
        if command == 0xA3:
            polling.set()
        return await original(command, *args, **kwargs)

    transport.request = request

    async def run():
        async with AsyncPrinter("test", transport=transport) as printer:
            await printer.send(make_job())

    task = asyncio.create_task(run())
    await asyncio.wait_for(polling.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.closed
    assert not any(cmd == 0xF3 for cmd, _ in transport.commands)


async def test_rejected_setup_preserves_error_result():
    class RejectTransport(FakeTransport):
        async def request(self, command, *args, **kwargs):
            packet = await super().request(command, *args, **kwargs)
            return Packet(packet.command, b"\x00") if command == 0x21 else packet

    transport = RejectTransport()
    async with AsyncPrinter("test", transport=transport) as printer:
        with pytest.raises(PrintError) as exc:
            await printer.send(make_job())
        assert exc.value.result["outcome"] == "error"
        assert not exc.value.result["instructions_sent"]
    assert not any(cmd == 0x01 for cmd, _ in transport.commands)


async def test_multiple_jobs_share_a_session():
    transport = FakeTransport()
    async with AsyncPrinter("test", transport=transport) as printer:
        first = await printer.send(make_job(copies=2))
        second = await printer.send(make_job())
        assert first["pages_printed"] == 2
        assert second["pages_printed"] == 1
    assert sum(cmd == 0xC1 for cmd, _ in transport.commands) == 1
