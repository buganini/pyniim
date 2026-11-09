import asyncio

from ..printer import AsyncPrinter, ensure_sync_context, require_blocking
from .bleak import discover_devices


def check_backend(identifier):
    if identifier not in (None, "bleak"):
        raise ValueError("Only the bleak backend is implemented")


async def discover_async(backend_identifier="bleak", *, timeout=5.0):
    check_backend(backend_identifier)
    if timeout <= 0:
        raise ValueError("Timeout must be positive")
    return await discover_devices(timeout)


def discover(backend_identifier="bleak", *, timeout=5.0):
    ensure_sync_context()
    return asyncio.run(discover_async(backend_identifier, timeout=timeout))


async def send_async(
    instructions,
    printer_identifier=None,
    backend_identifier=None,
    blocking=True,
    *,
    timeout=60.0,
    write_delay_ms=10.0,
):
    check_backend(backend_identifier)
    require_blocking(blocking)
    instructions.validate()
    if not printer_identifier:
        raise ValueError("printer_identifier is required")
    async with AsyncPrinter(
        printer_identifier, instructions.model, timeout=timeout, write_delay_ms=write_delay_ms
    ) as printer:
        return await printer.send(instructions)


def send(
    instructions,
    printer_identifier=None,
    backend_identifier=None,
    blocking=True,
    *,
    timeout=60.0,
    write_delay_ms=10.0,
):
    ensure_sync_context()
    return asyncio.run(
        send_async(
            instructions,
            printer_identifier,
            backend_identifier,
            blocking,
            timeout=timeout,
            write_delay_ms=write_delay_ms,
        )
    )
