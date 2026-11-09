"""Versioned JSON jobs; bitmap bytes use 1=black, MSB first, padded per row."""

import base64
import json
from dataclasses import dataclass
from pathlib import Path

from .models import ModelsManager

MAX_JOB_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Page:
    width: int
    height: int
    bitmap: bytes

    def validate(self):
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("Page dimensions must be integers")
        if not (1 <= self.width <= 567 and 1 <= self.height <= 65535):
            raise ValueError("Page dimensions exceed the experimental M2_H limits")
        stride = (self.width + 7) // 8
        if not isinstance(self.bitmap, bytes) or len(self.bitmap) != stride * self.height:
            raise ValueError("Bitmap length does not match page dimensions")
        if self.width % 8:
            mask = (1 << (8 - self.width % 8)) - 1
            if any(b & mask for b in self.bitmap[stride - 1 :: stride]):
                raise ValueError("Unused row padding bits must be white")

    def image(self):
        from PIL import Image, ImageOps

        return ImageOps.invert(
            Image.frombytes("1", (self.width, self.height), self.bitmap).convert("L")
        )


@dataclass(frozen=True)
class PrintJob:
    model: str
    label: str
    pages: tuple[Page, ...]
    density: int = 3
    copies: int = 1
    label_type: int = 1

    def validate(self):
        from .labels import LabelsManager

        model = ModelsManager().get_model_by_identifier(self.model)
        model.require_print_support()
        if (
            type(self.density) is not int
            or not model.density_min <= self.density <= model.density_max
        ):
            raise ValueError("Density is outside the model's supported range")
        if type(self.copies) is not int or not 1 <= self.copies <= 65535:
            raise ValueError("Copies must be an integer between 1 and 65535")
        # Only gap stock is enabled until other media modes have hardware tests.
        if type(self.label_type) is not int or self.label_type != 1:
            raise ValueError("Only gap labels (label_type=1) are currently implemented")
        if not self.pages or len(self.pages) * self.copies > 65535:
            raise ValueError("Job must contain 1..65535 total printed pages")
        expected = LabelsManager().get_label_by_identifier(self.label).pixels(model.dpi)
        for page in self.pages:
            page.validate()
            if (page.width, page.height) != expected:
                raise ValueError("Page dimensions do not match label size and model DPI")

    def save(self, path):
        self.validate()
        data = dict(
            format="pyniim",
            version=1,
            model=self.model,
            label=self.label,
            density=self.density,
            copies=self.copies,
            label_type=self.label_type,
            pages=[
                dict(width=p.width, height=p.height, bitmap=base64.b64encode(p.bitmap).decode())
                for p in self.pages
            ],
        )
        encoded = json.dumps(data, separators=(",", ":")).encode()
        if len(encoded) + 1 > MAX_JOB_BYTES:
            raise ValueError("Job exceeds 32 MiB file limit")
        Path(path).write_bytes(encoded + b"\n")

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_JOB_BYTES + 1)
        if len(raw) > MAX_JOB_BYTES:
            raise ValueError("Job exceeds 32 MiB file limit")
        try:
            data = json.loads(raw)
            version = data.pop("version")
            if data.pop("format") != "pyniim" or type(version) is not int or version != 1:
                raise ValueError("Unsupported job format or version")
            data["pages"] = tuple(
                Page(p["width"], p["height"], base64.b64decode(p["bitmap"], validate=True))
                for p in data["pages"]
            )
            job = cls(**data)
            job.validate()
            return job
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError(f"Invalid pyniim job: {exc}") from exc
