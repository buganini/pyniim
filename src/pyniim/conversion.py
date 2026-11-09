import math

from PIL import Image, ImageOps

from .job import Page, PrintJob
from .labels import LabelsManager


def convert(
    raster,
    images,
    label,
    *,
    rotate="auto",
    threshold=70,
    dither=False,
    density=None,
    copies=1,
    label_type=1,
):
    """Convert images without I/O to the printer. Fixed stock requires exact pixels."""
    model = raster.model_obj
    model.require_print_support()
    width, height = LabelsManager().get_label_by_identifier(label).pixels(model.dpi)
    if not 1 <= width <= model.printable_pixels or not 1 <= height <= 65535:
        raise ValueError("Label dimensions exceed model limits")
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError("Threshold must be between 0 and 100")
    if rotate not in ("auto", 0, 90, 180, 270, "0", "90", "180", "270"):
        raise ValueError("Rotate must be auto, 0, 90, 180 or 270")
    pages = []
    for source in images:
        if isinstance(source, Image.Image):
            im = source.copy()
        else:
            with Image.open(source) as opened:
                im = opened.copy()
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, "white")
        im = Image.alpha_composite(bg, rgba).convert("L")
        angle = (
            (90 if im.size != (width, height) and im.size == (height, width) else 0)
            if rotate == "auto"
            else int(rotate)
        )
        if angle:
            im = im.rotate(angle, expand=True)
        if im.size != (width, height):
            raise ValueError(f"Image is {im.size}; label {label} requires {(width, height)} pixels")
        # Match brother_ql threshold semantics; invert so protocol bit 1 means black.
        inverted = ImageOps.invert(im)
        if dither:
            mono = inverted.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        else:
            cutoff = int((100 - threshold) / 100 * 255)
            mono = inverted.point(lambda value: 0 if value < cutoff else 255, mode="1")
        bitmap = bytearray(mono.tobytes())
        stride = (width + 7) // 8
        if width % 8:
            mask = (0xFF << (8 - width % 8)) & 0xFF
            for index in range(stride - 1, len(bitmap), stride):
                bitmap[index] &= mask
        pages.append(Page(width, height, bytes(bitmap)))
    job = PrintJob(
        model.identifier,
        label,
        tuple(pages),
        model.density_default if density is None else density,
        copies,
        label_type,
    )
    job.validate()
    raster.job = job
    return job
