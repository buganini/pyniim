class PyniimError(Exception):
    """Base error for device and protocol failures."""


class ProtocolError(PyniimError):
    pass


class PrinterError(ProtocolError):
    """An explicit device error, distinct from an uncertain transport failure."""

    def __init__(self, code):
        self.code = code
        names = {
            0x01: "cover open",
            0x02: "out of paper",
            0x03: "low battery",
            0x04: "battery error",
            0x05: "cancelled by user",
            0x06: "data error",
            0x07: "overheated",
            0x08: "paper output error",
            0x09: "printer busy",
            0x0A: "printhead missing",
            0x0B: "temperature too low",
            0x0C: "printhead loose",
            0x0D: "ribbon missing",
            0x0E: "wrong ribbon",
            0x0F: "used ribbon",
            0x10: "wrong paper",
            0x17: "disconnected",
            0x34: "data receive timeout",
        }
        super().__init__(f"Printer error 0x{code:02x}: {names.get(code, 'unknown device error')}")


class UnsupportedModel(PyniimError):
    pass


class PrintError(PyniimError):
    """A failed or uncertain print; result preserves the last known progress."""

    def __init__(self, message, result):
        super().__init__(message)
        self.result = result
