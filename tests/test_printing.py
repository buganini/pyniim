import asyncio

import pytest
from PIL import Image

from pyniim import AsyncPrinter, Printer
from pyniim.backends.bleak import BleakTransport
from pyniim.backends.helpers import send_async
from pyniim.conversion import convert
from pyniim.errors import PrintError, ProtocolError
from pyniim.protocol import FrameDecoder, Packet, parse_print_status
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


@pytest.mark.parametrize("length", [4, 8, 10, 11])
def test_print_status_layouts(length):
    data = b"\x00\x02\x32\x64" + bytes([0x02]) * (length - 4)
    state = parse_print_status(data)
    assert state["pages"] == 2
    assert state["print_percent"] == 50
    assert state["feed_percent"] == 100
    assert state["raw"] == data.hex()
    assert state["error_code"] == (2 if length == 10 else None)


def test_truncated_print_status():
    with pytest.raises(ProtocolError, match="Truncated print status"):
        parse_print_status(bytes(3))


@pytest.mark.parametrize("code, description", [(2, "out of paper"), (0xFE, "unknown device error")])
@pytest.mark.parametrize("pages", [1, 2])
async def test_status_error_overrides_completion_and_preserves_progress(code, description, pages):
    class ErrorStatusTransport(FakeTransport):
        async def request(self, command, *args, **kwargs):
            reply = await super().request(command, *args, **kwargs)
            if command == 0xA3:
                return Packet(0xB3, reply.data + bytes((0, 0, code, 0, 0, 0)))
            return reply

    transport = ErrorStatusTransport()
    async with AsyncPrinter("test", transport=transport) as printer:
        with pytest.raises(PrintError, match=description) as exc:
            await printer.send(make_job(pages=pages))
        result = exc.value.result
        assert result["outcome"] == "error"
        assert result["pages_printed"] == 1
        assert result["did_print"]
        assert result["instructions_sent"] == (pages == 1)
        assert result["printer_state"]["error_code"] == code
        assert not result["ready_for_next_job"]
        with pytest.raises(ProtocolError):
            await printer.send(make_job())
    commands = [command for command, _ in transport.commands]
    assert commands.count(0x03) == 1
    assert 0xF3 not in commands
    assert transport.closed


async def test_ten_byte_status_without_error_completes():
    class ExtendedStatusTransport(FakeTransport):
        async def request(self, command, *args, **kwargs):
            reply = await super().request(command, *args, **kwargs)
            return Packet(0xB3, reply.data + bytes(6)) if command == 0xA3 else reply

    async with AsyncPrinter("test", transport=ExtendedStatusTransport()) as printer:
        result = await printer.send(make_job())
    assert result["outcome"] == "printed"
    assert result["printer_state"]["error_code"] == 0


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


@pytest.mark.parametrize("payload", [b"\x01", b"\x02", b"\x03", b"\x03\x00"])
async def test_connection_success_variants_complete_identification(payload):
    class ConnectTransport(FakeTransport):
        async def request(self, command, *args, **kwargs):
            packet = await super().request(command, *args, **kwargs)
            if command == 0xC1:
                assert kwargs["prefix"] == b"\x03"
                return Packet(0xC2, payload)
            return packet

    transport = ConnectTransport()
    async with AsyncPrinter("test", transport=transport) as printer:
        assert printer.info["model_id"] == 4608
        assert (await printer.send(make_job()))["outcome"] == "printed"
    assert transport.closed


@pytest.mark.parametrize(
    "payload, message",
    [
        (b"", "Empty"),
        (b"\x00", "disconnected"),
        (b"\x5a", "firmware errors"),
        (b"\xff", "unknown result"),
    ],
)
async def test_invalid_connection_result_closes_session(payload, message):
    class ConnectTransport(FakeTransport):
        async def request(self, command, *args, **kwargs):
            packet = await super().request(command, *args, **kwargs)
            return Packet(0xC2, payload) if command == 0xC1 else packet

    transport = ConnectTransport()
    with pytest.raises(ProtocolError, match=message):
        async with AsyncPrinter("test", transport=transport):
            pass
    assert transport.closed
    assert [command for command, _ in transport.commands] == [0xC1]


async def test_connect_success_code_is_not_a_print_command_ack():
    class BadAckTransport(FakeTransport):
        async def request(self, command, *args, **kwargs):
            packet = await super().request(command, *args, **kwargs)
            return Packet(packet.command, b"\x03") if command == 0x21 else packet

    async with AsyncPrinter("test", transport=BadAckTransport()) as printer:
        with pytest.raises(PrintError, match="Command 0x21 rejected: 03"):
            await printer.send(make_job())


async def test_m2h_connect03_and_page_size0100_reach_print_completion():
    class M2HTransport(FakeTransport):
        async def request(self, command, *args, **kwargs):
            packet = await super().request(command, *args, **kwargs)
            if command == 0xC1:
                return Packet(0xC2, b"\x03")
            if command == 0x13:
                # Framed representation of the user's reported 0x14 payload 01 00.
                raw = bytes.fromhex("55551402010017aaaa")
                decoder = FrameDecoder()
                assert decoder.feed(raw[:5]) == []
                return decoder.feed(raw[5:])[0]
            return packet

    transport = M2HTransport()
    async with AsyncPrinter("test", transport=transport) as printer:
        result = await printer.send(make_job())
    assert result["outcome"] == "printed"
    commands = [cmd for cmd, _ in transport.commands]
    assert commands.index(0x13) < commands.index(0x85) < commands.index(0xE3)
    assert commands[-2:] == [0xA3, 0xF3]
    assert transport.closed


@pytest.mark.parametrize("payload", [b"", b"\x00", b"\x00\x00", b"\x01\x01", b"\x01\x00\x00"])
async def test_unrecognized_page_size_ack_does_not_upload_rows(payload):
    class BadSizeTransport(FakeTransport):
        async def request(self, command, *args, **kwargs):
            packet = await super().request(command, *args, **kwargs)
            return Packet(0x14, payload) if command == 0x13 else packet

    transport = BadSizeTransport()
    async with AsyncPrinter("test", transport=transport) as printer:
        with pytest.raises(PrintError, match="Command 0x13 rejected") as exc:
            await printer.send(make_job())
        assert not exc.value.result["did_print"]
        assert not exc.value.result["instructions_sent"]
    assert not any(cmd in (0x84, 0x85, 0xE3, 0xF3) for cmd, _ in transport.commands)


@pytest.mark.parametrize("command", [0x21, 0x23, 0x01, 0x03, 0xE3, 0xF3])
async def test_page_size_ack_variant_does_not_relax_other_commands(command):
    from pyniim.tasks import ack

    class Transport:
        async def request(self, *args, **kwargs):
            return Packet(kwargs["response"], b"\x01\x00")

    with pytest.raises(ProtocolError, match="rejected: 0100"):
        await ack(Transport(), command, response=command + 1)


@pytest.mark.parametrize("delay", [0, 10, 25])
async def test_ble_writes_are_paced_between_chunks_and_frames(monkeypatch, delay):
    events = []

    class Client:
        is_connected = True

        async def write_gatt_char(self, characteristic, raw, response):
            events.append(("write", raw))

    async def sleep(seconds):
        events.append(("sleep", seconds))

    monkeypatch.setattr("pyniim.backends.bleak.asyncio.sleep", sleep)
    transport = BleakTransport("test", write_delay_ms=delay)
    transport.client = Client()
    transport.characteristic = FakeCharacteristic()
    first = Packet(0x84, b"\x00\x00\x01").encode()
    second = Packet(0xE3, b"\x01").encode()
    await transport.write(first)
    await transport.write(second)
    chunks = [first[:5], first[5:], second[:5], second[5:]]
    expected = []
    for chunk in chunks:
        expected.append(("write", chunk))
        if delay:
            expected.append(("sleep", delay / 1000))
    assert events == expected


@pytest.mark.parametrize("delay", [-1, float("nan"), float("inf")])
def test_invalid_write_delays_rejected(delay):
    with pytest.raises(ValueError, match="Write delay"):
        BleakTransport("test", write_delay_ms=delay)


async def test_page_end_timeout_names_command_and_expected_response(caplog):
    caplog.set_level("DEBUG", logger="pyniim")
    transport = BleakTransport("test", timeout=0.005, write_delay_ms=0)
    transport.characteristic = FakeCharacteristic()
    transport.client = FakeClient(transport, respond=False)
    with pytest.raises(TimeoutError, match="0xe3.*0xe4"):
        await transport.request(0xE3, response=0xE4)
    assert "TX cmd=0xe3" in caplog.text
    assert "Timed out after" in caplog.text
    assert transport.failure is not None


async def test_protocol_logs_include_response_payload(caplog):
    caplog.set_level("DEBUG", logger="pyniim")
    transport = BleakTransport("test", write_delay_ms=0)
    transport.characteristic = FakeCharacteristic()
    transport.client = FakeClient(transport)
    await transport.request(0x21, response=0x31)
    assert "TX cmd=0x21 data=01" in caplog.text
    assert "RX cmd=0x31 data=01" in caplog.text


@pytest.mark.parametrize("command", [0x00, 0xDB])
async def test_unsolicited_error_between_writes_stops_upload(command):
    transport = BleakTransport("test", write_delay_ms=0)
    transport.characteristic = FakeCharacteristic()
    transport.client = FakeClient(transport, respond=False)
    transport._notify(None, Packet(command, b"\x02").encode())
    with pytest.raises(ProtocolError, match=f"0x{command:02x}"):
        await transport.write(Packet(0x84, b"\x00\x00\x01").encode())
    assert transport.client.writes == []


async def test_error_during_chunked_write_stops_remaining_chunks():
    transport = BleakTransport("test", write_delay_ms=0)
    transport.characteristic = FakeCharacteristic()

    class ErrorClient(FakeClient):
        async def write_gatt_char(self, characteristic, raw, response):
            self.writes.append(raw)
            transport._notify(None, Packet(0xDB, b"\x02").encode())

    transport.client = ErrorClient(transport)
    with pytest.raises(ProtocolError, match="printer error"):
        await transport.request(0x21, response=0x31)
    assert len(transport.client.writes) == 1
    assert transport.pending is None


async def test_empty_timeout_has_page_end_context():
    class EmptyTimeoutTransport(FakeTransport):
        async def request(self, command, *args, **kwargs):
            if command == 0xE3:
                raise TimeoutError()
            return await super().request(command, *args, **kwargs)

    async with AsyncPrinter("test", transport=EmptyTimeoutTransport()) as printer:
        with pytest.raises(PrintError, match="PageEnd.*0xe3 -> 0xe4.*timed out"):
            await printer.send(make_job())
