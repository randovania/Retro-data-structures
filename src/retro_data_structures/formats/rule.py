from __future__ import annotations

import enum
import typing

import construct

from retro_data_structures.adapters.enum_adapter import EnumAdapter
from retro_data_structures.base_resource import AssetType, BaseResource, Dependency
from retro_data_structures.common_types import AssetId32, FourCC
from retro_data_structures.construct_extensions.alignment import AlignTo
from retro_data_structures.construct_extensions.misc import ErrorWithMessage

if typing.TYPE_CHECKING:
    from retro_data_structures.game_check import Game


class ComparisonOperator(enum.IntEnum):
    LessThan = 0
    LessThanOrEqualTo = 1
    EqualTo = 2
    GreaterThanOrEqualTo = 3
    GreaterThan = 4


class ValueType(enum.IntEnum):
    Bool = 0
    Float = 1
    Int32 = 2


RuleCondition = construct.Struct(
    id=FourCC,
    operator=EnumAdapter(ComparisonOperator, construct.Int8ub),
    value_type=EnumAdapter(ValueType, construct.Int8ub),
    value=construct.Switch(
        construct.this.value_type,
        {
            ValueType.Bool: construct.Int32ub,
            ValueType.Float: construct.Float32b,
            ValueType.Int32: construct.Int32ub,
        },
        default=ErrorWithMessage(lambda ctx: f"Unknown type: {ctx.value_type}"),
    ),
)

Action = construct.Struct(
    id=FourCC,
    properties=construct.PrefixedArray(construct.Int8ub, construct.Int32ub),
)

RuleSetRule = construct.Struct(
    conditions=construct.PrefixedArray(construct.Int16ub, RuleCondition),
    actions=construct.PrefixedArray(construct.Int16ub, Action),
)

RULE = construct.Struct(
    magic=construct.Const(b"RULE"),
    version=construct.Const(1, construct.Int8ub),
    parent_rule=AssetId32,
    rules=construct.PrefixedArray(
        construct.Int16ub,
        RuleSetRule,
    ),
    _align=AlignTo(32),
    end=construct.Terminated,
)


class RuleSet(BaseResource):
    @classmethod
    def resource_type(cls) -> AssetType:
        return "SAND"

    @classmethod
    def construct_class(cls, target_game: Game) -> construct.Construct:
        return RULE

    def dependencies_for(self) -> typing.Iterator[Dependency]:
        yield Dependency("RULE", self._raw.parent_rule)
