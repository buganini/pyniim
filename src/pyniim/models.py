"""Attributed model catalog. Catalog membership does not imply print support."""

import json
from dataclasses import dataclass
from importlib.resources import files

from .errors import UnsupportedModel


@dataclass(frozen=True)
class Model:
    identifier: str
    ids: tuple[int, ...]
    dpi: int
    print_direction: str
    printhead_pixels: int
    paper_types: tuple[str, ...]
    density_min: int
    density_max: int
    density_default: int

    @property
    def support(self):
        return "experimental" if self.identifier == "M2_H" else "catalog-only"

    @property
    def printable_pixels(self):
        # Conservative M2_H limit until 567/576 discrepancy is measured on hardware.
        return 567 if self.identifier == "M2_H" else self.printhead_pixels

    def require_print_support(self):
        if self.support == "catalog-only":
            raise UnsupportedModel(
                f"{self.identifier} is catalog-only; printing is not implemented"
            )


_catalog = json.loads(files("pyniim").joinpath("data/models.json").read_text())
SOURCE_REVISION = _catalog["revision"]
ALL_MODELS = tuple(
    Model(**{**m, "ids": tuple(m["ids"]), "paper_types": tuple(m["paper_types"])})
    for m in _catalog["models"]
)


class ModelsManager:
    def iter_identifiers(self):
        return (m.identifier for m in ALL_MODELS)

    def get_model_by_identifier(self, identifier):
        normalized = identifier.upper().replace("-", "_").replace(" ", "_")
        for model in ALL_MODELS:
            if model.identifier == normalized:
                return model
        raise UnsupportedModel(f"Unknown model: {identifier}")
