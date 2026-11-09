import importlib
import json

import pytest
from click.testing import CliRunner
from PIL import Image

from pyniim.cli import cli
from pyniim.conversion import convert
from pyniim.errors import PrintError
from pyniim.raster import NiimbotRaster


@pytest.fixture(params=["print", "send"])
def command(request, tmp_path):
    image = Image.new("RGB", (12, 12), "black")
    if request.param == "print":
        path = tmp_path / "label.png"
        image.save(path)
        return ["print", "-l", "1x1", str(path)]
    path = tmp_path / "job.niim"
    convert(NiimbotRaster(), [image], "1x1").save(path)
    return ["send", str(path)]


@pytest.mark.parametrize("outcome", ["printed", "error", "unknown"])
@pytest.mark.parametrize("as_json", [True, False])
def test_print_result_output(monkeypatch, command, outcome, as_json):
    progress = dict(
        outcome=outcome,
        pages_printed=1,
        did_print=True,
        instructions_sent=True,
        ready_for_next_job=outcome == "printed",
        printer_state={"raw": "00016464000002000000", "error_code": 2},
    )

    def send(*args, **kwargs):
        if outcome != "printed":
            raise PrintError("injected failure", progress)
        return progress

    monkeypatch.setattr(importlib.import_module("pyniim.cli"), "send", send)
    args = ["-p", "ble://test", *command, *(["--json"] if as_json else [])]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == (0 if outcome == "printed" else 1), result.output
    if as_json:
        expected = progress if outcome == "printed" else dict(progress, error="injected failure")
        assert json.loads(result.stdout) == expected
        assert result.stderr == ""
    elif outcome != "printed":
        assert f"outcome={outcome}" in result.stderr
        assert "pages_printed=1 (last reported)" in result.stderr


def test_json_missing_printer_has_no_fabricated_progress(command):
    result = CliRunner().invoke(cli, [*command, "--json"], env={"PYNIIM_PRINTER": ""})
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "outcome": "error",
        "error": "Specify --printer or PYNIIM_PRINTER",
    }


def test_json_preflight_failure(monkeypatch, command):
    def send(*args, **kwargs):
        raise ValueError("injected model mismatch")

    monkeypatch.setattr(importlib.import_module("pyniim.cli"), "send", send)
    result = CliRunner().invoke(cli, ["-p", "ble://test", *command, "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"outcome": "error", "error": "injected model mismatch"}
