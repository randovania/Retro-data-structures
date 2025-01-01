from __future__ import annotations

import copy
import dataclasses
import math
import typing

from retro_data_structures.properties.base_property import BaseProperty

if typing.TYPE_CHECKING:
    import typing_extensions

    from retro_data_structures import json_util


@dataclasses.dataclass()
class BaseVector(BaseProperty):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @classmethod
    def from_json(cls, data: json_util.JsonValue) -> typing_extensions.Self:
        json_data = typing.cast(dict[str, float], data)
        return cls(json_data["x"], json_data["y"], json_data["z"])

    def to_json(self) -> json_util.JsonObject:
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
        }

    def dependencies_for(self, asset_manager):
        yield from []

    def __add__(self, other: BaseVector) -> typing_extensions.Self:
        return self.__class__(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: BaseVector) -> typing_extensions.Self:
        return self.__class__(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, other: int | float | BaseVector) -> typing_extensions.Self:
        if isinstance(other, BaseVector):
            return self.__class__(self.x * other.x, self.y * other.y, self.z * other.z)
        if isinstance(other, int | float):
            return self.__class__(self.x * other, self.y * other, self.z * other)
        raise TypeError

    def __truediv__(self, other: int | float | BaseVector) -> typing_extensions.Self:
        if isinstance(other, BaseVector):
            return self.__class__(self.x / other.x, self.y / other.y, self.z / other.z)
        if isinstance(other, int | float):
            return self.__class__(self.x / other, self.y / other, self.z / other)
        raise TypeError

    def __floordiv__(self, other: int | float | BaseVector) -> typing_extensions.Self:
        if isinstance(other, BaseVector):
            return self.__class__(self.x // other.x, self.y // other.y, self.z // other.z)
        if isinstance(other, int | float):
            return self.__class__(self.x // other, self.y // other, self.z // other)
        raise TypeError

    def rotate(self, rotation: BaseVector, center: BaseVector | None = None) -> typing_extensions.Self:
        """
        Rotates the vector on all three axes, around a center point.

        :param rotation: The angle (in degrees) to rotate around each axis
        :param center: The point around which to revolve
        :returns: A new vector with the rotation applied
        """

        if center is None:
            center = BaseVector()

        pos = [self.x - center.x, self.y - center.y, self.z - center.z]
        rot = [rotation.x, rotation.y, rotation.z]

        for i in range(3):
            theta = rot[i] * math.pi / 180.0
            sin = math.sin(theta)
            cos = math.cos(theta)

            old_pos = copy.copy(pos)

            comp1 = (i + 1) % 3
            comp2 = (i + 2) % 3
            pos[comp1] = old_pos[comp1] * cos - old_pos[comp2] * sin
            pos[comp2] = old_pos[comp1] * sin + old_pos[comp2] * cos

        return self.__class__(pos[0], pos[1], pos[2]) + center
