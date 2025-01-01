from __future__ import annotations

import pytest

from retro_data_structures.properties.base_vector import BaseVector


def test_vector_operations():
    a = BaseVector(2.0, 3.0, 4.0)
    b = BaseVector(1.25, 1.5, 1.75)

    assert a + b == BaseVector(3.25, 4.5, 5.75)
    assert a - b == BaseVector(0.75, 1.5, 2.25)
    assert a * b == BaseVector(2.5, 4.5, 7.0)
    assert a / b == BaseVector(1.6, 2.0, 4.0 / 1.75)
    assert a // b == BaseVector(1, 2, 2)


@pytest.mark.parametrize(
    ("center", "expected"),
    (
        (BaseVector(0.0, 0.0, 0.0), BaseVector(x=-3.5355339059327378, y=0.7071067811865481, z=-0.9999999999999998)),
        (BaseVector(1.0, -3.0, 2.0), BaseVector(x=-3.2426406871192857, y=-5.82842712474619, z=2.0000000000000004)),
        (BaseVector(2.4, 0.3, -24.03), BaseVector(x=-17.91517782348951, y=18.211014767455254, z=-22.63)),
    ),
)
def test_rotation(center: BaseVector, expected: BaseVector):
    pos = BaseVector(1.0, 2.0, 3.0)
    rot = BaseVector(45.0, 90.0, 180.0)

    result = pos.rotate(rot, center)
    assert result == expected
