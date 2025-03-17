from __future__ import annotations

import dataclasses
import typing

from retro_data_structures.properties.base_property import BaseProperty

if typing.TYPE_CHECKING:
    import typing_extensions

    from retro_data_structures import json_util


@dataclasses.dataclass()
class BaseColor(BaseProperty):
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
    a: float = 0.0

    @classmethod
    def from_json(cls, data: json_util.JsonValue) -> typing_extensions.Self:
        json_data = typing.cast("dict[str, float]", data)
        return cls(json_data["r"], json_data["g"], json_data["b"], json_data["a"])

    def to_json(self) -> json_util.JsonObject:
        return {
            "r": self.r,
            "g": self.g,
            "b": self.b,
            "a": self.a,
        }
