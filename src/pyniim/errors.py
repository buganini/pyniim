class PyniimError(Exception):
    """Base error for device and protocol failures."""


class ProtocolError(PyniimError):
    pass


class UnsupportedModel(PyniimError):
    pass


class PrintError(PyniimError):
    """A failed or uncertain print; result preserves the last known progress."""

    def __init__(self, message, result):
        super().__init__(message)
        self.result = result
