# Third-party references

## Model metadata

`src/pyniim/data/models.json` contains model facts imported from MultiMote/niimbluelib,
`src/printer_models.ts`, revision `35110a716c960448ee17a9270e4c3a131149866f`.

Source: https://github.com/MultiMote/niimbluelib/blob/35110a716c960448ee17a9270e4c3a131149866f/src/printer_models.ts

Copyright (c) 2024 MultiMote. MIT license text is included in
`src/pyniim/data/niimbluelib-LICENSE.txt` and distributed as package data.
The import utility records the source revision. The upstream catalog includes aliases
and model IDs that are shared by different names; catalog membership does not imply
BLE compatibility or successful printing.

## Protocol research

The Python implementation was written for this project using published protocol
information. References consulted on 2026-11-09:

- https://github.com/iscarelli/niimbot-web-bluetooth/blob/main/docs/protocol-v4.md
- https://github.com/MultiMote/niimbluelib/blob/main/src/print_tasks/B1PrintTask.ts
- https://github.com/MultiMote/niimbluelib/blob/35110a716c960448ee17a9270e4c3a131149866f/src/packets/enumerations.ts
- https://github.com/MultiMote/niimbluelib/blob/35110a716c960448ee17a9270e4c3a131149866f/src/packets/packet_parser.ts
- https://printers.niim.blue/hardware/niimbot-m2h/

The implementation uses the b1 task for M2_H, acknowledged setup commands, row
packets with a black-pixel count, and completion polling before PrintEnd. Upstream
hardware validation is not validation of this implementation. Consumable status
fields and the 567/576-pixel width discrepancy remain unverified here.

Connection negotiation uses the first response byte as a `ConnectResult` enum:
1 (`Connected`), 2 (`ConnectedNew`), and 3 (`ConnectedV3`) are successful.
This differs from the boolean ACK expected for print commands. An M2_H user
reported response `03`; this confirms that response occurs on hardware, not that
the complete print workflow has been verified.

The same user subsequently reported payload `01 00` for the M2_H SetPageSize
response (`0x13` request, `0x14` response). The implementation accepts this exact
variant alongside the single-byte `01` ACK for SetPageSize only. The second byte
is not interpreted, and other commands retain their existing validation. This
is a hardware-observed response variant, not evidence of completed printing;
completion still requires the print-status counter and PrintEnd acknowledgement.

A subsequent user log shows valid outgoing frames covering rows 0–353 followed
by PageEnd (`0xe3`), with no completion or response payload recorded. It does not
establish that the printer received all rows. Pacing is informed by niimbluelib's
10 ms packet interval and the write-without-response discussion in
https://github.com/iscarelli/niimbot-web-bluetooth/blob/main/src/niimbot.js .
The default 10 ms delay is a mitigation to test, not a confirmed diagnosis.
Device error response IDs (`0x00` unsupported, `0xdb` print error) are documented in
https://github.com/MultiMote/niimbluelib/blob/35110a716c960448ee17a9270e4c3a131149866f/src/packets/commands.ts .

The user subsequently reported successful physical printing on 2026-11-09.
See [the hardware test record](docs/HARDWARE_TESTING.md) for the scope of that
evidence and details not yet recorded. The preceding paragraphs document the
earlier troubleshooting attempts.

Print-status decoding follows `parsePrintStatusResponse` in the pinned
`packet_parser.ts`: responses have at least four progress bytes; exactly
10-byte responses carry a printer error at offset 6. Error descriptions use
the pinned `PrinterErrorCode` enumeration. Unknown error values remain visible
as numbers, and other response lengths retain their raw data without guessing
an error-field position. Fault semantics have not yet been exercised on M2_H
hardware by this project.

## API design

The module organization and CLI conventions reference:
https://github.com/pklaus/brother_ql

No Brother raster opcodes or Brother model/label tables are used. The job container
and BLE execution model differ from Brother's raw instruction stream.
