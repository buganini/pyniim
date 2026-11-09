import functools
import json
import logging
import platform
from dataclasses import asdict
from pathlib import Path

import click
from bleak.exc import BleakError

from . import Printer, __version__
from .backends.helpers import discover as discover_printers
from .backends.helpers import send
from .conversion import convert
from .errors import PyniimError
from .job import PrintJob
from .labels import LabelsManager
from .models import ALL_MODELS, SOURCE_REVISION, ModelsManager
from .raster import NiimbotRaster


def checked(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (PyniimError, BleakError, ValueError, OSError, TimeoutError) as exc:
            raise click.ClickException(str(exc)) from exc

    return wrapped


def emit(value, as_json):
    if as_json:
        click.echo(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, list):
        for item in value:
            click.echo(json.dumps(item, ensure_ascii=False))
    else:
        click.echo(json.dumps(value, ensure_ascii=False, indent=2))


def printer_id(ctx):
    if not ctx.obj["printer"]:
        raise click.UsageError("Specify --printer or PYNIIM_PRINTER")
    return ctx.obj["printer"]


@click.group()
@click.option(
    "-b", "--backend", type=click.Choice(["bleak"]), default="bleak", envvar="PYNIIM_BACKEND"
)
@click.option("-m", "--model", default="M2_H", envvar="PYNIIM_MODEL")
@click.option("-p", "--printer", envvar="PYNIIM_PRINTER")
@click.option("--debug", is_flag=True)
@click.version_option(__version__)
@click.pass_context
def cli(ctx, backend, model, printer, debug):
    """Experimental NIIMBOT BLE printing; M2_H only, not hardware verified."""
    ctx.obj = dict(backend=backend, model=model, printer=printer)
    logging.basicConfig(level=logging.DEBUG if debug else logging.WARNING)


@cli.command()
@click.option("--timeout", type=click.FloatRange(min=0, min_open=True), default=5.0)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
@checked
def discover(ctx, timeout, as_json):
    """Scan BLE advertisements (includes unrecognized devices)."""
    emit(discover_printers(ctx.obj["backend"], timeout=timeout), as_json)


@cli.group()
def info():
    """Inspect model, label and environment information."""


@info.command()
@click.option("--json", "as_json", is_flag=True)
def models(as_json):
    emit(
        [
            dict(**asdict(m), support=m.support, printable_pixels=m.printable_pixels)
            for m in ALL_MODELS
        ],
        as_json,
    )


@info.command()
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
@checked
def labels(ctx, as_json):
    model = ModelsManager().get_model_by_identifier(ctx.obj["model"])
    manager = LabelsManager()
    sizes = [manager.get_label_by_identifier(s) for s in manager.iter_identifiers()]
    emit(
        [
            dict(label=s.identifier, pixels=s.pixels(model.dpi), example_only=True)
            for s in sizes
            if s.pixels(model.dpi)[0] <= model.printable_pixels
        ],
        as_json,
    )


@info.command()
@click.option("--json", "as_json", is_flag=True)
def env(as_json):
    emit(
        dict(
            pyniim=__version__,
            python=platform.python_version(),
            platform=platform.platform(),
            catalog_revision=SOURCE_REVISION,
        ),
        as_json,
    )


def image_options(function):
    decorators = [
        click.argument(
            "images", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False)
        ),
        click.option("-l", "--label", required=True, envvar="PYNIIM_LABEL"),
        click.option(
            "-r", "--rotate", type=click.Choice(["auto", "0", "90", "180", "270"]), default="auto"
        ),
        click.option("-t", "--threshold", type=click.FloatRange(0, 100), default=70.0),
        click.option("-d", "--dither", is_flag=True),
        click.option("--density", type=int),
        click.option("--copies", type=click.IntRange(1, 65535), default=1),
        click.option(
            "--label-type", type=click.Choice(["1"]), default="1", help="1 = labels with gaps"
        ),
    ]
    for decorator in reversed(decorators):
        function = decorator(function)
    return function


def make_job(ctx, **options):
    options["label_type"] = int(options["label_type"])
    return convert(NiimbotRaster(ctx.obj["model"]), **options)


@cli.command("print")
@image_options
@click.pass_context
@checked
def print_cmd(ctx, **options):
    """Convert images and wait for confirmed printing."""
    identifier = printer_id(ctx)
    job = make_job(ctx, **options)
    emit(send(job, identifier, ctx.obj["backend"]), False)


@cli.command("convert")
@image_options
@click.option("-o", "--output", required=True, type=click.Path(dir_okay=False))
@click.pass_context
@checked
def convert_cmd(ctx, output, **options):
    """Save an offline version-1 .niim JSON job."""
    job = make_job(ctx, **options)
    job.save(output)
    click.echo(output)


@cli.command("send")
@click.argument("instructions", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
@checked
def send_cmd(ctx, instructions):
    """Execute a .niim job; its model must match the connected printer."""
    identifier = printer_id(ctx)
    job = PrintJob.load(instructions)
    # An explicitly selected model must agree with the file; the default is not an override.
    if ctx.parent.get_parameter_source("model") != click.core.ParameterSource.DEFAULT:
        if ModelsManager().get_model_by_identifier(ctx.obj["model"]).identifier != job.model:
            raise ValueError("Selected model does not match job")
    emit(send(job, identifier, ctx.obj["backend"]), False)


@cli.command()
@click.argument("instructions", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--output-dir", type=click.Path(file_okay=False), help="Write page previews as PNG files"
)
@click.option("--json", "as_json", is_flag=True)
@checked
def analyze(instructions, output_dir, as_json):
    """Validate a job and optionally render its bitmap pages."""
    job = PrintJob.load(instructions)
    if output_dir:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(job.pages, 1):
            page.image().save(directory / f"page-{index:04d}.png")
    emit(
        dict(
            model=job.model,
            label=job.label,
            pages=len(job.pages),
            copies=job.copies,
            density=job.density,
            label_type=job.label_type,
        ),
        as_json,
    )


@cli.command()
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
@checked
def status(ctx, as_json):
    """Read raw heartbeat data; M2_H consumable semantics remain unverified."""
    with Printer(printer_id(ctx), model=ctx.obj["model"]) as printer:
        emit(printer.status(), as_json)
