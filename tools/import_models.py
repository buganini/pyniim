"""Import a local, pinned niimbluelib printer_models.ts snapshot (MIT)."""

import argparse
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("revision")
parser.add_argument("license", type=Path)
args = parser.parse_args()
models = []
for block in re.findall(r"\{\s*model: PrinterModel\.(.*?)\n  \}", args.source.read_text(), re.S):
    name = block.split(",", 1)[0]

    def integer(key):
        return int(re.search(rf"\b{key}: (\d+)", block)[1])

    models.append(
        dict(
            identifier=name,
            ids=json.loads(re.search(r"id: (\[[^\]]+\])", block)[1]),
            dpi=integer("dpi"),
            print_direction=re.search(r'printDirection: "(\w+)"', block)[1],
            printhead_pixels=integer("printheadPixels"),
            paper_types=re.findall(r"LT\.(\w+)", block),
            density_min=integer("densityMin"),
            density_max=integer("densityMax"),
            density_default=integer("densityDefault"),
        )
    )
if not models:
    raise SystemExit("No models parsed")
dest = Path(__file__).resolve().parents[1] / "src/pyniim/data"
(dest / "models.json").write_text(
    json.dumps(
        dict(
            source="https://github.com/MultiMote/niimbluelib",
            revision=args.revision,
            models=models,
        ),
        indent=2,
    )
    + "\n"
)
(dest / "niimbluelib-LICENSE.txt").write_text(args.license.read_text())
print(f"Imported {len(models)} model records")
