"""Experimental M2_H b1 task. No hardware validation has been performed here."""

import asyncio

from .errors import PrintError, ProtocolError
from .protocol import row_packets


async def ack(transport, command, data=b"\x01", *, response, timeout=None):
    reply = await transport.request(command, data, response=response, timeout=timeout)
    if reply.data != b"\x01":
        raise ProtocolError(f"Command 0x{command:02x} rejected: {reply.data.hex()}")


async def identify(transport):
    await ack_connect(transport)
    status = await transport.request(0xA5, response=0xB5)
    info = {}
    for sub in (0x08, 0x0B, 0x0D, 0x0A, 0x07, 0x03, 0x0C, 0x09):
        reply = await transport.request(0x40, bytes((sub,)), response=0x40 + sub)
        info[f"{sub:02x}"] = reply.data.hex()
    raw_id = bytes.fromhex(info["08"])
    if len(raw_id) not in (1, 2):
        raise ProtocolError("Invalid printer model ID response")
    model_id = int.from_bytes(raw_id.ljust(2, b"\x00"), "big")
    heartbeat = await transport.request(0xDC, b"\x04", response=(0xD9, 0xDD, 0xDE, 0xDF))
    return dict(
        model_id=model_id,
        status_data=status.data.hex(),
        info=info,
        heartbeat=dict(command=heartbeat.command, data=heartbeat.data.hex()),
    )


async def ack_connect(transport):
    reply = await transport.request(0xC1, response=0xC2, prefix=b"\x03")
    # ConnectResult is an enum, not the boolean ACK used by print commands.
    # niimbluelib: Connected=1, ConnectedNew=2, ConnectedV3=3.
    # Its parser reads the first byte and permits additional payload bytes.
    if not reply.data:
        raise ProtocolError("Empty printer connection response")
    result = reply.data[0]
    if result not in (1, 2, 3):
        reason = {0: "disconnected", 90: "firmware errors"}.get(result, "unknown result")
        raise ProtocolError(f"Printer refused connection: {reply.data.hex()} ({reason})")


async def print_job(transport, job, *, timeout=60.0):
    job.validate()
    result = dict(
        instructions_sent=False,
        outcome="unknown",
        printer_state=None,
        did_print=False,
        ready_for_next_job=False,
        pages_printed=0,
    )
    total = len(job.pages) * job.copies
    started = False
    try:
        await ack(transport, 0x21, bytes((job.density,)), response=0x31)
        await ack(transport, 0x23, bytes((job.label_type,)), response=0x33)
        started = True  # An unacknowledged start can already have changed the device state.
        await ack(transport, 0x01, total.to_bytes(2, "big") + bytes(5), response=0x02)
        target = 0
        for index, page in enumerate(job.pages):
            await ack(transport, 0x03, response=0x04)
            size = (
                page.height.to_bytes(2, "big")
                + page.width.to_bytes(2, "big")
                + job.copies.to_bytes(2, "big")
            )
            await ack(transport, 0x13, size, response=0x14)
            for packet in row_packets(page):
                await transport.write(packet.encode())
            await ack(transport, 0xE3, response=0xE4, timeout=timeout)
            if index == len(job.pages) - 1:
                result.update(instructions_sent=True, outcome="sent")
            target += job.copies
            # Wait after every uploaded page to avoid overrunning device storage.
            async with asyncio.timeout(timeout):
                while True:
                    reply = await transport.request(0xA3, response=0xB3)
                    if len(reply.data) < 4:
                        raise ProtocolError("Truncated print status")
                    count = int.from_bytes(reply.data[:2], "big")
                    if count > total:
                        raise ProtocolError(
                            "Unexpected print counter; completion cannot be confirmed"
                        )
                    result["printer_state"] = dict(
                        pages=count,
                        print_percent=reply.data[2],
                        feed_percent=reply.data[3],
                        raw=reply.data.hex(),
                    )
                    result["pages_printed"] = count
                    result["did_print"] = count > 0
                    if count >= target:
                        break
                    await asyncio.sleep(0.25)
        # Never end a job while its physical printing remains unconfirmed.
        await ack(transport, 0xF3, response=0xF4)
        result.update(outcome="printed", ready_for_next_job=True)
        return result
    except (Exception, asyncio.CancelledError) as exc:
        # Do not blindly retry or send PrintEnd on a timeout: either can spoil labels.
        result["outcome"] = "unknown" if started else "error"
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise PrintError(f"Print failed or is uncertain: {exc}", result) from exc
