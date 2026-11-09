import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Label:
    identifier: str
    width_mm: float
    height_mm: float

    def pixels(self, dpi):
        return tuple(math.floor(mm * dpi / 25.4 + 0.5) for mm in (self.width_mm, self.height_mm))


class LabelsManager:
    """Dimensions are user-supplied stock sizes, not validated product SKUs."""

    def iter_identifiers(self):
        return iter(("30x20", "40x30", "40x40"))

    def get_label_by_identifier(self, identifier):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", identifier)
        if not match:
            raise ValueError("Label must be WIDTHxHEIGHT in mm, e.g. 40x30")
        width, height = map(float, match.groups())
        if not all(math.isfinite(n) and 0 < n <= 1000 for n in (width, height)):
            raise ValueError("Label dimensions must be between 0 and 1000 mm")
        return Label(identifier, width, height)
