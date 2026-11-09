# Hardware testing

## M2_H: initial user report, 2026-11-09

The user reported that physical printing now succeeds after the connection
response, SetPageSize acknowledgement, and BLE pacing fixes. The troubleshooting
sequence used an M2_H over Bleak, with earlier logs from the macOS CoreBluetooth
backend. This is initial single-label evidence; the successful run's full log
and exact command were not supplied.

| Item | Evidence or status |
| --- | --- |
| Device / transport | NIIMBOT M2_H / BLE using Bleak |
| Physical printing | User reported success |
| Earlier test input | `examples/test-label.png`, 472 × 354 px, requested label `40x30` |
| Connection response | `03` observed during troubleshooting |
| SetPageSize response | `01 00` observed during troubleshooting |
| Operating system | Earlier logs use macOS CoreBluetooth; OS version not recorded |
| Successful revision / settings | Not recorded; report followed the pacing fix in `2fee2e9` |
| Firmware / Python / Bleak versions | Not recorded for the successful run |
| Media and ribbon | Product identifiers and measured dimensions not recorded |
| Counter and PrintEnd confirmation | Successful run's log not supplied |
| Multiple copies / multiple pages | Simulated tests only |
| Paper, cover, ribbon, battery faults | Simulated/protocol coverage only |
| Linux / Windows BLE | Not tested on hardware |
| Margins / alignment / 567 px boundary | Not measured |

M2_H remains `experimental`. Other catalog entries remain `catalog-only` with
printing disabled. Earlier failure traces describe their respective attempts;
they do not contradict the later success report or identify the cause of the
original stall.

## Recording the next run

Record `git rev-parse HEAD`, `pyniim info env --json`,
`python -m pip show bleak Pillow click`, the printer firmware version, loaded
media and ribbon identifiers, and the exact command. Use the same Python
environment for all commands. Replace the placeholder with a discovered device:

```bash
pyniim --debug --write-delay-ms 10 -m M2_H -p 'ble://<device-id>' \
  print --json -l 40x30 examples/test-label.png \
  > result.json 2> protocol.log
```

Check the process exit code and result fields against the physical output.
Record whether the image is complete, upright, centered, and correctly sized.
Remove device addresses, UUIDs, and serial numbers before sharing logs.

After a single label succeeds, test `--copies 2`, then pass two image paths for
a two-page job. Count the labels and compare them with `pages_printed`; capture
the status and PrintEnd replies. Test error conditions separately and record
their raw replies, decoded errors, last known progress, and physical output.
A timeout or disconnect can leave the outcome unknown; inspect the printer
before manually starting another job.

Each new record should distinguish observations from assumptions. Neither a
successful unit test nor an upstream project's hardware report verifies this
implementation on a new printer, firmware, or platform.
