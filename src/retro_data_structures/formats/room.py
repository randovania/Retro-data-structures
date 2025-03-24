from __future__ import annotations

import typing
from dataclasses import dataclass

import construct
from construct import Container, Hex, Int16ul, Int32ul, PrefixedArray, Struct

from retro_data_structures.base_resource import AssetType, BaseResource, Dependency
from retro_data_structures.common_types import GUID
from retro_data_structures.construct_extensions.misc import ErrorWithMessage, UntilEof
from retro_data_structures.formats.chunk_descriptor import SingleTypeChunkDescriptor
from retro_data_structures.formats.form_descriptor import FormDescriptor
from retro_data_structures.properties.base_property import BaseProperty

if typing.TYPE_CHECKING:
    import uuid

    from retro_data_structures.game_check import Game
    from retro_data_structures.properties.prime_remastered.core.PooledString import PooledString

GreedyBytes = typing.cast("construct.Construct", construct.GreedyBytes)

LoadUnit = FormDescriptor(
    "LUNT",
    0,
    0,
    Struct(
        header=SingleTypeChunkDescriptor(
            "LUHD",
            Struct(
                name=construct.PascalString(Int32ul, "utf8"),
                guid=GUID,
                idB=GUID,
                unk1=construct.Int16ul,
                unk2=Int32ul,
                rest=GreedyBytes,
            ),
        ),
        load_resources=SingleTypeChunkDescriptor("LRES", construct.PrefixedArray(Int32ul, GUID)),
        load_layers=SingleTypeChunkDescriptor("LLYR", construct.PrefixedArray(Int32ul, GUID)),
    ),
)

PerformanceGroups = construct.PrefixedArray(
    construct.Int16ul,
    Struct(
        name=construct.PascalString(Int32ul, "utf8"),
        controller_id=GUID,
        unk1=construct.Flag,
        layer_guids=construct.PrefixedArray(construct.Int16ul, GUID),
    ),
)

GeneratedObjectMap = construct.PrefixedArray(
    construct.Int16ul,
    Struct(
        object_id=GUID,
        layer_id=GUID,
    ),
)

Docks = construct.PrefixedArray(
    construct.Int16ul,
    Struct(
        guid1=GUID,
        guid2=GUID,
        guid3=GUID,
        guid4=GUID,
        roomType1=construct.Int16ul,
        roomType2=construct.Int16ul,
    ),
)

DataEnumValue = Struct(
    unk1=Int32ul,
    idA=GUID,
    skip=construct.Prefixed(Int32ul, GreedyBytes),
    idB=GUID,
)

GameAreaHeader = Struct(
    id_a=GUID,
    unk1=construct.Int16ul,
    unk2=construct.Int16ul,
    unk3=construct.Flag,
    id_b=GUID,
    id_c=GUID,
    id_d=GUID,
    id_e=GUID,
    id_f=GUID,
    work_stages=Struct(
        items=construct.PrefixedArray(
            construct.Int16ul,
            Struct(
                type=Hex(construct.Int32ul),
                data=construct.Prefixed(
                    construct.Int16ul,
                    construct.Switch(
                        construct.this.type,
                        {
                            0x7B68B3A0: DataEnumValue,
                            0xB9187C94: DataEnumValue,
                            0xEC5D9069: DataEnumValue,
                            0x8333A604: DataEnumValue,
                            0x5CA42B9D: DataEnumValue,
                            # LdrToEnum_ProductionWorkStageEnu
                            0x113F9CE1: Hex(Int32ul),
                        },
                        # TODO: should actually be GreedyBytes since the game skips over unknown ids
                        ErrorWithMessage(lambda ctx: f"Unknown type: {ctx.type}"),
                    ),
                ),
            ),
        ),
    ),
)

BakedLightning = construct.Struct(
    "unk1" / Int32ul,  # always either 3 or 0?
    construct.StopIf(lambda ctx: ctx.unk1 == 0),
    construct.Check(lambda ctx: ctx.unk1 == 3),
    "resource1" / GUID,
    "unk2" / construct.PrefixedArray(Int32ul, GUID),
    "unk3"
    / construct.PrefixedArray(
        Int32ul, Struct("unk1" / Int32ul, "unk2" / Int32ul, "unk3" / Int32ul, "unk4" / construct.Const(0, Int32ul))
    ),
    "resource2" / GUID,
    construct.Terminated,
)

RoomHeader = FormDescriptor(
    "HEAD",
    0,
    0,
    Struct(
        game_area_header=SingleTypeChunkDescriptor("RMHD", GameAreaHeader),
        performance_groups=SingleTypeChunkDescriptor("PGRP", PerformanceGroups),
        generated_object_map=SingleTypeChunkDescriptor("LGEN", GeneratedObjectMap),
        docks=SingleTypeChunkDescriptor("DOCK", Docks),
        baked_lightinng=SingleTypeChunkDescriptor("BLIT", BakedLightning),
        load_units=construct.PrefixedArray(
            SingleTypeChunkDescriptor("LUNS", construct.Int16ul),
            LoadUnit,
        ),
    ),
)

STRP = Struct(
    unk=Int32ul,
    pools=construct.PrefixedArray(
        Int32ul,
        construct.Prefixed(
            Int32ul,
            GreedyBytes,
        ),
    ),
    z=GreedyBytes,
)

ConstructPooledString = Struct(
    a=construct.Int32sl,
    b=construct.IfThenElse(
        construct.this.a != -1,
        Int32ul,
        construct.Prefixed(Int32ul, GreedyBytes),
    ),
)

CalculateAllocatedMemoryForTypedefInterfaceSLdrFromCRC32 = Struct(
    a=construct.Prefixed(construct.Int16ul, GreedyBytes),
)

SizeofAllocationsForEventCriteriaSLdrFromStream = Struct(
    a=Int32ul,
    b=construct.If(
        construct.this.a != 0,
        Struct(
            a=CalculateAllocatedMemoryForTypedefInterfaceSLdrFromCRC32,
            b=construct.Prefixed(Int32ul, GreedyBytes),
        ),
    ),
)
SizeofAllocationsForActionPayloadSLdrFromStream = SizeofAllocationsForEventCriteriaSLdrFromStream
SizeofAllocationsForLinkDataSLdrFromStream = SizeofAllocationsForEventCriteriaSLdrFromStream


class RDSPropertyAdapter(construct.Adapter):
    def __init__(self, type_id):
        super().__init__(GreedyBytes)
        self.type_id = type_id

    def _decode(self, obj: bytes, context, path):
        import retro_data_structures.properties.prime_remastered.objects

        type_id = self.type_id(context)
        try:
            property_class = retro_data_structures.properties.prime_remastered.objects.get_object(type_id)
        except KeyError:
            return obj
        return property_class.from_bytes(obj)

    def _encode(self, obj: BaseProperty | bytes, context, path):
        if isinstance(obj, BaseProperty):
            return obj.to_bytes()
        return obj


class ComponentTypeName(int):
    def __str__(self) -> str:
        import retro_data_structures.properties.prime_remastered.objects

        try:
            return retro_data_structures.properties.prime_remastered.objects.get_object(self).__name__
        except KeyError:
            return f"0x{self:08x}"


class ComponentType(Hex):
    def _decode(self, obj, context, path):
        return ComponentTypeName(super()._decode(obj, context, path))


Property = Struct(
    type_id=ComponentType(Int32ul),
    data=RDSPropertyAdapter(construct.this.type_id),
)

ScriptData = Struct(
    sdhr=SingleTypeChunkDescriptor(
        "SDHR",
        Struct(
            properties_count=Int32ul,
            instance_data_count=Int32ul,
            weird_count=Int32ul,
            guids=construct.Array(construct.this.weird_count, GUID),
            unk=construct.Array(construct.this.weird_count, Struct(a=Int32ul, b=Int32ul)),
        ),
    ),
    properties=construct.Array(construct.this.sdhr.properties_count, SingleTypeChunkDescriptor("SDEN", Property)),
    instance_data=construct.Array(
        construct.this.sdhr.instance_data_count,
        SingleTypeChunkDescriptor(
            "IDTA",
            Struct(
                guid=GUID,
                str=ConstructPooledString,
                connections=construct.PrefixedArray(
                    construct.Int16ul,
                    Struct(
                        skip1=construct.Bytes(0x1A),
                        event_criteria_sldr=SizeofAllocationsForEventCriteriaSLdrFromStream,
                        action_payload_sldr=SizeofAllocationsForActionPayloadSLdrFromStream,
                        skip2=construct.Bytes(0x13),
                    ),
                ),
                script_links=construct.PrefixedArray(
                    construct.Int16ul,
                    Struct(
                        a=Int32ul,
                        b=GUID,
                        c=SizeofAllocationsForLinkDataSLdrFromStream,
                        skip2=construct.Bytes(0x12),
                    ),
                ),
                skip_the_rest=GreedyBytes,
            ),
        ),
    ),
)

GameObjectComponent = Struct(
    component_type=ComponentType(Int32ul),
    property_idx=Int32ul,
    instance_idx=Int32ul,
)
GeneratedGameObject = SingleTypeChunkDescriptor(
    "GGOB", Struct(generated_game_object_id=GUID, components=PrefixedArray(Int16ul, GameObjectComponent))
)

Layer = FormDescriptor(
    "LAYR",
    0,
    0,
    Struct(
        header=SingleTypeChunkDescriptor(
            "LHED",
            Struct(
                name=construct.PascalString(Int32ul, "utf8"),
                id=GUID,
                unk=Int32ul,
                id2=GUID,
                rest=GreedyBytes,
            ),
        ),
        generated_script_object=FormDescriptor("GSRP", 0, 0, UntilEof(GeneratedGameObject)),
        components=FormDescriptor("SRIP", 0, 0, SingleTypeChunkDescriptor("COMP", UntilEof(GameObjectComponent))),
    ),
)

ROOM = FormDescriptor(
    "ROOM",
    147,
    160,
    Struct(
        header=RoomHeader,
        strp=SingleTypeChunkDescriptor("STRP", STRP),
        script_data=FormDescriptor("SDTA", 0, 0, ScriptData),
        layers=FormDescriptor(
            "LYRS",
            0,
            0,
            construct.Array(
                lambda ctx: len(ctx._._.header.performance_groups[0].layer_guids),
                Layer,
            ),
        ),
    ),
)

T = typing.TypeVar("T")


@dataclass(frozen=True)
class Instance:
    guid: uuid.UUID
    properties: BaseProperty | Container


class Room(BaseResource):
    @classmethod
    def resource_type(cls) -> AssetType:
        return "ROOM"

    @classmethod
    def construct_class(cls, target_game: Game) -> construct.Construct:
        return ROOM

    def dependencies_for(self) -> typing.Iterator[Dependency]:
        yield from []

    def get_pooled_string(self, pooled_string: PooledString) -> bytes:
        if pooled_string.index == -1:
            return pooled_string.size_or_str
        else:
            return self.raw.strp.pools[0][pooled_string.index : pooled_string.index + pooled_string.size_or_str]

    def properties_of_type(self, t: type[T]) -> typing.Iterator[T]:
        for prop in self.raw.script_data.properties:
            if isinstance(prop.data, t):
                yield prop.data

    def test(self):
        sdhr = self.raw.script_data.sdhr
        weird_count = sdhr.weird_count
        guids = sdhr.guids
        weird = sdhr.unk
        instance_data = self.raw.script_data.instance_data
        properties = self.raw.script_data.properties

        instances: dict[int, Instance] = {}

        def _add_inst(inst):
            instances[inst.instance_idx] = Instance(
                instance_data[inst.instance_idx].guid, properties[inst.property_idx]
            )

        for layer in self.raw.layers:
            for comp in layer.components:
                _add_inst(comp)
            for generated in layer.generated_script_object:
                for comp in generated.components:
                    _add_inst(comp)

        count1 = 0
        count2 = 0
        for i in range(weird_count):
            guids[i]
            unk = weird[i]
            if (
                type(instances[unk.a].properties.data).__name__ != "EntityProperties"
                and type(instances[unk.b].properties.data).__name__ != "EntityProperties"
            ):
                print(f"i: {i}")
                print(f"a: {instances[unk.a]}")
                print(f"b: {instances[unk.b]}")
                count1 += 1
            else:
                count2 += 1

        print(count1)
        print(count2)
