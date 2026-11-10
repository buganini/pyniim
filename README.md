# pyniim

A Python library and CLI for NIIMBOT label printers, starting with **M2_H printing over BLE** using **Bleak**.

**The API and CLI are designed after [pklaus/brother_ql](https://github.com/pklaus/brother_ql)**, following its module organization, workflow, and common command-line options while adapting to the NIIMBOT protocol and BLE transport. Model support starts with the M2_H and will expand to other NIIMBOT printers.

This project is planned and developed with assistance from **GPT-6 Astra**.

> **Status: 0.1.0a1, experimental.** An M2_H user reported successful physical printing over BLE on 2026-11-09 after the connection, acknowledgement, and pacing fixes. This is initial single-label evidence, not a complete hardware qualification; see the [hardware test record](docs/HARDWARE_TESTING.md). Of the 77 model records, only the M2_H is enabled for experimental printing; the others are available for reference only.

## Installation and quick start

Requires Python 3.11 or later. Install from a local checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
pyniim --help
pyniim info models --json
```

These instructions install the local package; it has not been published to PyPI. BLE operations require Bluetooth permissions and an available BLE adapter. Offline conversion and model queries do not require a connection.

Use `pyniim discover --json` to find a device identifier, then pass it to `--printer`. Discovery lists all detected BLE advertisements, including devices not yet identified as NIIMBOT printers. The service and model ID are checked when connecting.

A 40 × 30 mm label requires a **472 × 354 px** image with the current M2_H 300 DPI settings. Fixed-size labels are not automatically resized. A size mismatch reports the expected pixel dimensions; `rotate=auto` only handles swapped width and height.

## Goals

- Discover, connect to, and control the M2_H through Bleak and BLE GATT.
- Offer a simple `print_image()` method and separate `convert()` / `send()` functions.
- Provide synchronous convenience APIs and asynchronous connection sessions.
- Follow `brother_ql` module organization, common parameters, and print result fields.
- Maintain a NIIMBOT model catalog that distinguishes recorded specifications, experimental support, and hardware verification.
- Distinguish completed printing from completed transmission, reporting device errors and uncertain outcomes.

## Input formats

Accepts image paths, Pillow `Image.Image` objects, and binary file objects readable by Pillow.

Pillow handles image decoding. Common formats include PNG, JPEG, BMP, GIF, and TIFF; availability depends on the installed Pillow build. Multi-frame images are not automatically expanded into multiple pages. Extract the frames first and pass an explicit list of images.

SVG, PDF, HTML, and text layout are not direct inputs in this release. Render them to raster images first. Barcodes, QR codes, and text can be drawn into a Pillow image using other tools before printing.

## Python API

The following interfaces are implemented. M2_H printing has an initial user-reported success; broader hardware validation is ongoing.

### Print an image directly

```python
from pyniim import Printer

with Printer("ble://<device-id>", model="M2_H") as printer:
    result = printer.print_image(
        "label.png",
        label="40x30",
        density=3,
        copies=1,
    )
```

`print_image()` handles conversion and printing internally. `label="40x30"` specifies dimensions in millimeters: width along the printhead × length along the feed direction. This is a usage example; choose settings that match the printer and loaded media.

### Convert and send separately

```python
from pyniim.raster import NiimbotRaster
from pyniim.conversion import convert
from pyniim.backends.helpers import discover, send

devices = discover(backend_identifier="bleak")

raster = NiimbotRaster("M2_H")
instructions = convert(
    raster,
    images=["label.png"],
    label="40x30",
    rotate="auto",
    threshold=70,
    dither=False,
    density=3,
    copies=1,
)

result = send(
    instructions,
    printer_identifier="ble://<device-id>",
    backend_identifier="bleak",
    blocking=True,
)
```

The printer cannot read PNG or JPEG files directly. `convert()` checks dimensions, applies rotation and black-and-white conversion, and prepares bitmap data with row padding. `send()` handles the device connection, protocol handshake, transmission, and completion checks.

`convert()` returns a `PrintJob` containing the model, pages, and print settings for step-by-step protocol execution. Unlike `brother_ql.convert()`, it does not return raw `bytes`. The APIs and instruction formats are not interchangeable.

`blocking=True` waits for confirmed completion. This release explicitly rejects `blocking=False`: closing a BLE session or sending PrintEnd too early can interrupt physical printing. Safe background job management is not implemented yet.

### Asynchronous usage

```python
import asyncio
from pyniim import AsyncPrinter

async def main():
    async with AsyncPrinter("ble://<device-id>", model="M2_H") as printer:
        result = await printer.print_image("label.png", label="40x30")
        return result

asyncio.run(main())
```

`pyniim.backends.helpers` also provides `discover_async()` and `send_async()`. A synchronous session keeps the BLE connection and event loop for its entire lifetime. Applications with an already-running event loop should use the asynchronous API.

### Print results

Results retain the main dictionary fields from `brother_ql.send()` and add `pages_printed`:

| Field | Meaning |
| --- | --- |
| `instructions_sent` | Whether all job data has been transmitted |
| `outcome` | `unknown`, `sent`, `printed`, or `error` |
| `printer_state` | Most recently received device state, or `None` if unknown |
| `did_print` | Whether the device reported evidence that printing occurred |
| `ready_for_next_job` | Whether the device was confirmed ready for another job |
| `pages_printed` | Last reported count of printed pages |

Successful BLE writes alone do not establish successful printing. A failure during printing raises `pyniim.errors.PrintError`; its `result` attribute preserves the last known progress. `did_print=True` can mean that only part of the job completed. Check `outcome == "printed"` for whole-job success. Jobs are not automatically resent after a timeout or disconnect, to avoid duplicate labels. A failed session cannot be reused; reconnect with a new session.

The known 10-byte print-status response includes an error code at byte offset 6.
`printer_state.error_code` is `0` for no reported error, a positive integer for a
device error, or `None` when the response layout has no decoded error field.
Nonzero codes stop the job with `outcome="error"`, even if the reported page count
has reached its target. Common codes have readable descriptions, and unknown
codes retain their numeric value. Raw status bytes remain available in
`printer_state.raw`. These protocol meanings come from upstream research;
individual fault conditions still need M2_H hardware testing.

## CLI

```bash
# Discover devices and inspect specifications
pyniim discover
pyniim info models
pyniim -m M2_H info labels
pyniim info env

# Print directly
pyniim -b bleak -m M2_H -p 'ble://<device-id>' \
  print -l 40x30 --density 3 --copies 1 label.png

# Query device status
pyniim -p 'ble://<device-id>' status

# Convert offline, preview, and send a job file
pyniim -m M2_H convert -l 40x30 label.png -o label.niim
pyniim analyze label.niim --output-dir previews
pyniim -p 'ble://<device-id>' send label.niim
```

A `.niim` file is a version-1 JSON job containing the model, label dimensions, density, copy count, and Base64-encoded page bitmaps. Files are limited to 32 MiB. Loading validates the format version, pixel dimensions, and data lengths. These files are neither Brother instruction files nor packet captures that can be written directly to BLE.

Supported environment variables are `PYNIIM_BACKEND`, `PYNIIM_MODEL`, `PYNIIM_PRINTER`, and `PYNIIM_LABEL`. The CLI supports `--debug` and `--json` output for query commands, `print`, and `send`.

### Scriptable print results

```bash
pyniim -m M2_H -p 'ble://<device-id>' \
  print --json -l 40x30 examples/test-label.png
pyniim -p 'ble://<device-id>' send --json label.niim
```

With `--json`, stdout contains one JSON object. Success returns the API result
fields and exit code 0. A print failure preserves those fields, adds an `error`
message, and exits with code 1. A connection or conversion failure before print
progress is available returns only `outcome="error"` and `error`. Argument parsing
errors (such as missing files or invalid options) still use Click's stderr output
and exit code 2. Debug logs go to stderr. Without `--json`, print failures include
the outcome and last reported page count in the error message.

### Debugging a stalled print

`--debug` logs timestamped protocol requests, received payloads, upload progress,
and completion polling. Add `--bleak-debug` to include low-level Bleak logs.
These are global options and belong before the subcommand:

```bash
pyniim --debug --write-delay-ms 10 -m M2_H -p 'ble://<device-id>' \
  print -l 40x30 examples/test-label.png 2>&1 | tee /tmp/pyniim-debug.log
```

BLE writes without response are paced with a **10 ms delay after every chunk**,
including the final image chunk before PageEnd. `--write-delay-ms 25` allows a
slower diagnostic run; `0` disables pacing. Python callers can set
`write_delay_ms` on `Printer`, `AsyncPrinter`, `send()`, or `send_async()`.
Pacing is intended to reduce device buffer overruns. The user reported successful
printing after these fixes, but the successful run's exact pacing setting was
not recorded, so the cause of the earlier stall is not established.

If PageEnd stalls, the error names the `0xe3` request and expected `0xe4` reply.
An upload-complete log means bytes were submitted to BLE, not that the printer
received or printed every row. A timeout does not automatically retry or send
PrintEnd. Device error notifications received during uploads also stop the job.

## Comparison with brother_ql

| brother_ql | pyniim |
| --- | --- |
| `BrotherQLRaster(model)` | `NiimbotRaster(model)` |
| `convert(qlr, images, label, **kwargs)` | `convert(raster, images, label, ...)` |
| `convert()` returns instruction `bytes` | Returns a structured `PrintJob` |
| `backends.helpers.discover()` | Same module path; defaults to the `bleak` backend |
| `backends.helpers.send()` | Retains the main parameters and result fields |
| `ModelsManager` / `LabelsManager` | Corresponding model and label queries |
| `discover`, `info`, `print`, `send`, `analyze` | Same command responsibilities |
| `-b`, `-m`, `-p`, `-l`, `-r`, `-t`, `-d` | Corresponding common option meanings |

The `convert` subcommand, `status`, `print_image()`, and asynchronous API are pyniim extensions. NIIMBOT-specific settings include density, copies, and label type. Brother-specific two-color, 600 DPI, and quality modes do not carry over directly. Compression and cutting support depend on each model's actual capabilities.

## Model catalog and support status

The catalog imports 77 specification records from [niimbluelib's model data](https://github.com/MultiMote/niimbluelib/blob/main/src/printer_models.ts), preserving its MIT license and source revision. Coverage includes NIIMBOT A, B, D, K, M, N, P, S, and T series, among others. Catalog membership does not imply BLE connectivity or working print support.

| Priority | Models | Current status |
| --- | --- | --- |
| Primary | M2_H (`M2-H` alias accepted) | Experimental; single-label BLE success reported by a user |
| Next | B1, B21, D11, D110 | Catalog only; printing disabled |
| Next | B1_PRO, B2_PRO, B21_PRO, D11_H, D110_M | Catalog only; printing disabled |
| Other | Remaining NIIMBOT models in the catalog | Catalog only; printing disabled |

`info models` uses the `catalog-only` and `experimental` support markers. The initial M2_H success and missing test details are recorded in [docs/HARDWARE_TESTING.md](docs/HARDWARE_TESTING.md). Firmware, operating system, media, and measured results should accompany future records. Unknown models and unimplemented protocols do not automatically fall back to M2_H settings.

Published M2_H information lists model ID 4608 and 300 DPI. The [community wiki](https://printers.niim.blue/hardware/niimbot-m2h/) lists a 576 px printhead; [another BLE implementation's hardware notes](https://github.com/iscarelli/niimbot-web-bluetooth/blob/main/docs/NOTES.md#m2-h-first-real-capture-2026-08-13) describe using 567 px to leave a ribbon margin. The catalog retains upstream specifications, while printing remains conservatively limited to 567 px. Usable boundaries still require physical testing here. Calibrated centering offsets and margins are not provided yet.

## BLE and internal architecture

The Bleak backend scans for a `BLEDevice`, connects, checks the service and characteristic, subscribes to notifications, and then performs identification and print commands. Writes are split according to the characteristic's capacity and paced between chunks. The receiver handles both fragmented notifications and multiple packets in a single notification.

`ble://<device-id>` uses the identifier returned by discovery. On macOS, this is typically a device UUID; it is not assumed to be a MAC address. See the [Bleak documentation](https://bleak.readthedocs.io/en/latest/api/client.html).

| Module | Responsibility |
| --- | --- |
| `models`, `labels` | Model capabilities, label dimensions, media types, and print limits |
| `conversion`, `raster` | Image processing and bitmap preparation |
| `protocol` | Packet encoding and decoding, checksums, and response parsing |
| `tasks` | Model-specific print workflows, flow control, and completion checks |
| `backends/bleak` | Discovery, GATT, notifications, and chunked writes |
| `printer`, `cli` | Public Python API and command-line interface |

## Initial release limitations

- Only M2_H printing is enabled, using labels with gaps (`label_type=1`).
- `status` returns raw heartbeat data. Paper, lid, ribbon, and battery fields are not decoded because their meanings have not been verified on M2_H.
- Multi-page jobs conservatively wait for each page to finish. Multiple copies use the device's copies field. Both workflows still require hardware testing.
- `blocking=False`, automatic resizing, continuous media, cutting, and printing on other models are not implemented.
- `info labels` lists example dimensions, not verified NIIMBOT media products.

## Development and verification

```bash
python -m pip install -e '.[dev]'
pytest -q
ruff check src tests tools
ruff format --check src tests tools
python -m build
```

Unit tests do not scan for or connect to real devices. They cover image inputs, bitmap polarity and bit order, job validation, offline CLI conversion, fragmented packets, chunked writes, model mismatches, timeouts, disconnections, and delayed responses.

[GitHub Actions](.github/workflows/ci.yml) runs the simulated tests on Linux,
macOS, and Windows with Python 3.11 and 3.14. A separate job checks Ruff, builds
the source distribution and wheel, and exercises the installed wheel's offline
CLI in a clean environment. These checks do not establish BLE hardware support
on those platforms.

Model data is pinned to niimbluelib revision `35110a716c960448ee17a9270e4c3a131149866f`. To update it, download `src/printer_models.ts` and `LICENSE` from the chosen revision, then run:

```bash
python tools/import_models.py /path/to/printer_models.ts <revision> /path/to/LICENSE
```

## Remaining validation

1. Record a reproducible successful M2_H run with firmware, runtime versions, settings, and completion logs.
2. Test multiple copies and multiple pages; measure orientation, dimensions, and boundaries, and verify completion reporting.
3. Test out-of-paper conditions, open lids, ribbon problems, and disconnections during printing.
4. Verify BLE compatibility on macOS, Linux, and Windows.
5. Expand model and media support based on hardware evidence.

The first user-reported physical print is complete. The next hardware milestone is a reproducible run with measured output and confirmed completion reporting, followed by copies, multiple pages, and fault testing through both the API and CLI.

## References

- [brother_ql](https://github.com/pklaus/brother_ql) ([GPLv3](https://github.com/pklaus/brother_ql/blob/master/LICENSE)): API and CLI design reference; not a runtime dependency.
- [niimbluelib](https://github.com/MultiMote/niimbluelib) ([MIT](https://github.com/MultiMote/niimbluelib/blob/35110a716c960448ee17a9270e4c3a131149866f/LICENSE)): Imported NIIMBOT model data and protocol implementation reference.
- [NIIMBOT Community Wiki](https://printers.niim.blue/): Hardware and protocol research; content license not confirmed.
- [niimbot-web-bluetooth](https://github.com/iscarelli/niimbot-web-bluetooth) ([MIT](https://github.com/iscarelli/niimbot-web-bluetooth/blob/main/LICENSE)): BLE print workflows and model validation reference.

This project is licensed under the [MIT License](LICENSE). Model data comes from MIT-licensed niimbluelib; its original copyright notice and full license are distributed in `src/pyniim/data/niimbluelib-LICENSE.txt`. The Brother API is used as an interface design reference. See [THIRD_PARTY.md](THIRD_PARTY.md) for protocol research sources and attribution.
