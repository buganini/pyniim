import io
import json

import pytest
from click.testing import CliRunner
from PIL import Image

from pyniim.cli import cli
from pyniim.conversion import convert
from pyniim.errors import ProtocolError, UnsupportedModel
from pyniim.job import Page, PrintJob
from pyniim.labels import LabelsManager
from pyniim.models import ALL_MODELS, ModelsManager
from pyniim.protocol import FrameDecoder, Packet, row_packets
from pyniim.raster import NiimbotRaster


def image_job(color="white", **options):
    return convert(NiimbotRaster(), [Image.new("RGB", (12, 12), color)], "1x1", **options)


def test_frame_known_vector_and_every_split():
    raw = bytes.fromhex("5555c10101c1aaaa")
    assert Packet(0xC1, b"\x01").encode() == raw
    for split in range(len(raw) + 1):
        decoder = FrameDecoder()
        assert decoder.feed(raw[:split]) + decoder.feed(raw[split:]) == [Packet(0xC1, b"\x01")]
    assert FrameDecoder().feed(b"noise" + raw * 2) == [Packet(0xC1, b"\x01")] * 2


def test_bad_checksum_and_trailer():
    for offset in (5, 7):
        raw = bytearray(Packet(0xC1, b"\x01").encode())
        raw[offset] ^= 1
        with pytest.raises(ProtocolError):
            FrameDecoder().feed(raw)


def test_row_encoding_and_run_limit():
    assert list(row_packets(Page(8, 201, bytes(201)))) == [
        Packet(0x84, b"\x00\x00\xc8"),
        Packet(0x84, b"\x00\xc8\x01"),
    ]
    assert list(row_packets(Page(9, 1, b"\x80\x80"))) == [
        Packet(0x85, b"\x00\x00\x00\x02\x00\x01\x80\x80")
    ]


def test_conversion_polarity_alpha_padding_and_preview():
    white = image_job()
    black = image_job("black")
    assert white.pages[0].bitmap == bytes(24)
    assert black.pages[0].bitmap == b"\xff\xf0" * 12
    assert black.pages[0].image().getpixel((0, 0)) == 0
    transparent = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
    assert convert(NiimbotRaster(), [transparent], "1x1").pages[0].bitmap == bytes(24)


def test_file_inputs_rotation_and_size(tmp_path):
    image = Image.new("RGB", (24, 12), "black")
    file = tmp_path / "input.png"
    image.save(file)
    for source in (str(file), image, io.BytesIO(file.read_bytes())):
        assert convert(NiimbotRaster(), [source], "1x2").pages[0].width == 12
    with pytest.raises(ValueError, match="requires"):
        convert(NiimbotRaster(), [image], "1x1")
    with pytest.raises(ValueError, match="exceed"):
        convert(NiimbotRaster(), [image], "60x30")


@pytest.mark.parametrize(
    "options",
    [
        {"copies": 0},
        {"copies": True},
        {"density": 6},
        {"label_type": 2},
        {"threshold": float("nan")},
        {"rotate": 45},
    ],
)
def test_conversion_rejects_invalid_options(options):
    with pytest.raises(ValueError):
        image_job(**options)


def test_catalog_is_not_print_support():
    assert len(ALL_MODELS) == 77
    assert ModelsManager().get_model_by_identifier("M2-H").identifier == "M2_H"
    with pytest.raises(UnsupportedModel, match="catalog-only"):
        convert(NiimbotRaster("B1"), [], "40x30")
    assert LabelsManager().get_label_by_identifier("40x30").pixels(300) == (472, 354)


def test_job_roundtrip_and_validation(tmp_path):
    job = image_job("black", copies=2)
    path = tmp_path / "job.niim"
    job.save(path)
    assert PrintJob.load(path) == job
    data = json.loads(path.read_text())
    data["pages"][0]["height"] = 13
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Bitmap length"):
        PrintJob.load(path)
    path.write_text('{"format": "pyniim", "version": 999}')
    with pytest.raises(ValueError, match="version"):
        PrintJob.load(path)
    with pytest.raises(ValueError, match="padding"):
        Page(1, 1, b"\xff").validate()


def test_cli_offline_pipeline(tmp_path):
    runner = CliRunner()
    image = tmp_path / "input.png"
    job = tmp_path / "job.niim"
    previews = tmp_path / "previews"
    Image.new("RGB", (12, 12), "black").save(image)
    result = runner.invoke(cli, ["convert", "-l", "1x1", str(image), "-o", str(job)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli, ["analyze", str(job), "--output-dir", str(previews), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["pages"] == 1
    assert Image.open(previews / "page-0001.png").getpixel((0, 0)) == 0
    result = runner.invoke(cli, ["info", "models", "--json"])
    assert len(json.loads(result.output)) == 77
    result = runner.invoke(cli, ["send", str(job)])
    assert result.exit_code != 0
    assert "--printer" in result.output


@pytest.mark.parametrize("command", ["print", "send"])
def test_cli_passes_write_delay_to_transport(monkeypatch, tmp_path, command):
    import importlib

    module = importlib.import_module("pyniim.cli")
    calls = []

    def send(job, identifier, backend, **options):
        calls.append((identifier, backend, options))
        return {"outcome": "printed"}

    monkeypatch.setattr(module, "send", send)
    if command == "print":
        path = tmp_path / "image.png"
        Image.new("RGB", (12, 12), "black").save(path)
        args = ["print", "-l", "1x1", str(path)]
    else:
        path = tmp_path / "job.niim"
        image_job().save(path)
        args = ["send", str(path)]
    result = CliRunner().invoke(cli, ["--write-delay-ms", "25", "-p", "ble://test", *args])
    assert result.exit_code == 0, result.output
    assert calls == [("ble://test", "bleak", {"write_delay_ms": 25.0})]
