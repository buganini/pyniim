import asyncio

from .backends.bleak import BleakTransport
from .conversion import convert
from .errors import ProtocolError
from .models import ModelsManager
from .raster import NiimbotRaster
from .tasks import identify, print_job


def require_blocking(blocking):
    if blocking is not True:
        raise ValueError("blocking=False is not implemented; completion must be awaited safely")


def ensure_sync_context():
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("An event loop is running; use AsyncPrinter or the async helper")


class AsyncPrinter:
    def __init__(self, identifier, model="M2_H", *, timeout=60.0, transport=None):
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        self.model = ModelsManager().get_model_by_identifier(model)
        self.model.require_print_support()
        self.transport = transport if transport is not None else BleakTransport(identifier)
        self.timeout = timeout
        self.info = None
        self.opened = False
        self.used = False
        self.lock = asyncio.Lock()
        self.failed = False

    async def __aenter__(self):
        if self.used:
            raise RuntimeError("Create a new printer session to reconnect")
        self.used = True
        try:
            await self.transport.open()
            self.info = await identify(self.transport)
            if self.info["model_id"] not in self.model.ids:
                raise ProtocolError(
                    f"Expected {self.model.identifier}; device reports model ID {self.info['model_id']}"
                )
            self.opened = True
            return self
        except BaseException:
            await self.transport.close()
            raise

    async def __aexit__(self, *args):
        self.opened = False
        await self.transport.close()

    def _check(self):
        if not self.opened or self.failed:
            raise ProtocolError("Use a connected, healthy printer session")

    async def status(self):
        async with self.lock:
            self._check()
            reply = await self.transport.request(0xDC, b"\x04", response=(0xD9, 0xDD, 0xDE, 0xDF))
            return dict(
                model=self.model.identifier,
                model_id=self.info["model_id"],
                raw_heartbeat=dict(command=reply.command, data=reply.data.hex()),
                interpretation="raw; consumable fields are not verified on M2_H",
            )

    async def send(self, instructions, *, blocking=True):
        require_blocking(blocking)
        instructions.validate()
        if instructions.model != self.model.identifier:
            raise ValueError("Job model does not match session model")
        async with self.lock:
            self._check()
            try:
                return await print_job(self.transport, instructions, timeout=self.timeout)
            except BaseException:
                self.failed = True
                raise

    async def print_image(self, image, *, label, **options):
        job = convert(NiimbotRaster(self.model.identifier), [image], label, **options)
        return await self.send(job)


class Printer:
    """Synchronous session with one event loop for the entire BLE lifetime."""

    def __init__(self, identifier, model="M2_H", **options):
        self.printer = AsyncPrinter(identifier, model, **options)
        self.runner = None

    def __enter__(self):
        ensure_sync_context()
        if self.runner is not None:
            raise RuntimeError("Session is already open")
        self.runner = asyncio.Runner()
        try:
            self.runner.run(self.printer.__aenter__())
        except BaseException:
            self.runner.close()
            self.runner = None
            raise
        return self

    def __exit__(self, *args):
        try:
            self.runner.run(self.printer.__aexit__(*args))
        finally:
            self.runner.close()
            self.runner = None

    def _check(self):
        ensure_sync_context()
        if self.runner is None:
            raise RuntimeError("Use Printer as a context manager")

    def print_image(self, image, *, label, **options):
        self._check()
        return self.runner.run(self.printer.print_image(image, label=label, **options))

    def send(self, instructions, *, blocking=True):
        self._check()
        return self.runner.run(self.printer.send(instructions, blocking=blocking))

    def status(self):
        self._check()
        return self.runner.run(self.printer.status())
