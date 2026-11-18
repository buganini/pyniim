import asyncio

from ..models import ALL_MODELS
from ..printer import AsyncPrinter, ensure_sync_context, require_blocking
from .bleak import discover_devices


def check_backend(identifier):
    if identifier not in (None, "bleak"):
        raise ValueError("Only the bleak backend is implemented")


def supported_model_for_name(name):
    """Guess a print-supported model from an advertised name such as ``M2_H-<serial>``."""
    if not name:
        return None
    upper = name.upper()
    for model in ALL_MODELS:
        if model.support != "catalog-only" and upper.startswith(f"{model.identifier}-"):
            return model.identifier
    return None


async def discover_async(backend_identifier="bleak", *, timeout=5.0, include_unsupported=False):
    check_backend(backend_identifier)
    if timeout <= 0:
        raise ValueError("Timeout must be positive")
    devices = [
        dict(device, model=supported_model_for_name(device["name"]))
        for device in await discover_devices(timeout)
    ]
    if include_unsupported:
        return devices
    return [device for device in devices if device["model"]]


def discover(backend_identifier="bleak", *, timeout=5.0, include_unsupported=False):
    ensure_sync_context()
    return asyncio.run(
        discover_async(backend_identifier, timeout=timeout, include_unsupported=include_unsupported)
    )


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
