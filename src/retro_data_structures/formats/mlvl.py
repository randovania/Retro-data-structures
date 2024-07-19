"""
Wiki: https://wiki.axiodl.com/w/MLVL_(File_Format)
"""

from __future__ import annotations

import itertools
import typing

import construct
from construct import (
    Adapter,
    Array,
    Bitwise,
    Const,
    Container,
    CString,
    Error,
    Flag,
    Float32b,
    FocusedSeq,
    Int8ub,
    Int16ub,
    Int32ub,
    ListContainer,
    Peek,
    PrefixedArray,
    Sequence,
    Struct,
    Switch,
)

from retro_data_structures.base_resource import AssetType, BaseResource, Dependency, NameOrAssetId
from retro_data_structures.common_types import AssetId32, AssetId64, Vector3
from retro_data_structures.construct_extensions.misc import PrefixedArrayWithExtra
from retro_data_structures.exceptions import UnknownAssetId
from retro_data_structures.formats import Mapw
from retro_data_structures.formats.guid import GUID
from retro_data_structures.formats.mrea import (
    Area,
    AreaDependencies,
    AreaDependencyAdapter,
    AreaModuleDependencyAdapter,
)
from retro_data_structures.formats.savw import Savw
from retro_data_structures.formats.strg import Strg
from retro_data_structures.game_check import Game

if typing.TYPE_CHECKING:
    from collections.abc import Iterator

MLVLConnectingDock = Struct(
    area_index=Int32ub,
    dock_index=Int32ub,
)

MLVLDock = Struct(
    connecting_dock=PrefixedArray(Int32ub, MLVLConnectingDock),
    dock_coordinates=PrefixedArray(Int32ub, Vector3),
)

MLVLMemoryRelay = Struct(
    memory_relay_index=Int32ub,
    target_index=Int32ub,
    message=Int16ub,
    active=Int8ub,
)


class LayerFlags(Adapter):
    def __init__(self):
        super().__init__(
            Struct(
                layer_count=Int32ub,
                layer_flags=Bitwise(Array(64, Flag)),
            )
        )

    def _decode(self, obj: Container, context, path) -> list[bool]:
        return ListContainer(reversed(obj.layer_flags))[: obj.layer_count]

    def _encode(self, obj: list[bool], context, path) -> Container:
        flags = [True for i in range(64)]
        flags[: len(obj)] = obj
        return Container({"layer_count": len(obj), "layer_flags": list(reversed(flags))})


def create_area(version: int, asset_id):
    area_fields = [
        "area_name_id" / asset_id,
        "area_transform" / Array(12, Float32b),
        "area_bounding_box" / Array(6, Float32b),
        "area_mrea_id" / asset_id,
        "internal_area_id" / asset_id,
    ]

    # DKCR
    if version < 0x1B:
        area_fields.append("attached_area_index" / PrefixedArray(Int32ub, Int16ub))

    # Corruption
    if version < 0x19:
        area_fields.append("dependencies" / AreaDependencyAdapter(asset_id))

    area_fields.append("docks" / PrefixedArray(Int32ub, MLVLDock))

    # Echoes
    if version == 0x17:
        area_fields.append("module_dependencies" / AreaModuleDependencyAdapter())

    # DKCR
    if version >= 0x1B:
        # Unknown, always 0?
        area_fields.append(Const(0, Int32ub))

    # Prime 2 Demo
    if version >= 0x14:
        area_fields.append("internal_area_name" / CString("utf-8"))

    return Struct(*area_fields)


def create(version: int, asset_id):
    area = create_area(version, asset_id)

    fields = [
        "magic" / Const(0xDEAFBABE, Int32ub),
        "version" / Const(version, Int32ub),
        "world_name_id" / asset_id,
    ]

    # Prime 2
    if version == 0x17:
        fields.append("dark_world_name_id" / asset_id)

    # Prime 2 and 3
    if 0x17 <= version <= 0x19:
        fields.append("temple_key_world_index" / Int32ub)

    # TODO: time attack for DKCR

    fields.extend(
        [
            "world_save_info_id" / asset_id,
            "default_skybox_id" / asset_id,
        ]
    )

    # Prime 1
    if version <= 0x11:
        # Array describing all outgoing Memory Relay connections in this world.
        # Memory Relays connected to multiple objects are listed multiple times.
        fields.append("memory_relays" / PrefixedArray(Int32ub, MLVLMemoryRelay))

    # Prime 1
    if version <= 0x11:
        # Extra field is unknown, always 1
        fields.append("areas" / PrefixedArrayWithExtra(Int32ub, Const(1, Int32ub), area))
    else:
        fields.append("areas" / PrefixedArray(Int32ub, area))

    # DKCR
    if version <= 0x1B:
        fields.append("world_map_id" / asset_id)  # MAPW

        # This is presumably the same unknown value as at the beginning of the SCLY format. Always 0.
        fields.append("unknown_scly_field" / Const(0, Int8ub))

        # The MLVL format embeds a script layer. This script layer is used in the MP1 demo for storing Dock instances,
        # but it's unused in all retail builds, so this is always 0.
        fields.append("script_instance_count" / Const(0x0, Int32ub))

    # Prime 1
    if version <= 0x11:
        fields.append(
            "audio_group"
            / PrefixedArray(
                Int32ub,
                Struct(
                    group_id=Int32ub,
                    agsc_id=asset_id,
                ),
            )
        )

        # Unknown purpose, always empty
        fields.append(CString("utf-8"))

    fields.extend(
        [
            "area_layer_flags" / PrefixedArray(Int32ub, LayerFlags()),
            "_layer_names"
            / construct.Rebuild(
                PrefixedArray(Int32ub, CString("utf-8")),
                lambda ctx: list(itertools.chain.from_iterable(ctx.area_layer_names)),
            ),
        ]
    )

    # Corruption
    if version >= 0x19:
        fields.append("layer_guid" / PrefixedArray(Int32ub, GUID))

    fields.append(
        "_area_layer_name_offset"
        / construct.Rebuild(
            PrefixedArray(Int32ub, Int32ub),
            lambda ctx: [0] + list(itertools.accumulate(len(it) for it in ctx.area_layer_names[:-1])),
        )
    )
    fields.append(
        "area_layer_names"
        / construct.Computed(
            lambda ctx: [
                ctx._layer_names[start:end]
                for start, end in zip(
                    ctx._area_layer_name_offset, ctx._area_layer_name_offset[1:] + [len(ctx._layer_names)], strict=True
                )
            ]
        )
    )

    return Struct(*fields)


Prime1MLVL = create(0x11, AssetId32)
Prime2MLVL = create(0x17, AssetId32)
Prime3MLVL = create(0x19, AssetId64)

MLVL = FocusedSeq(
    "mlvl",
    header=Peek(Sequence(Int32ub, Int32ub)),
    mlvl=Switch(
        lambda this: this.header[1] if this._parsing else this.mlvl.version,
        {
            0x11: Prime1MLVL,
            0x17: Prime2MLVL,
            0x19: Prime3MLVL,
        },
        Error,
    ),
)


class Mlvl(BaseResource):
    _mapw: Mapw = None
    _savw: Savw = None

    @classmethod
    def resource_type(cls) -> AssetType:
        return "MLVL"

    @classmethod
    def construct_class(cls, target_game: Game) -> construct.Construct:
        return MLVL

    def dependencies_for(self) -> typing.Iterator[Dependency]:
        for area in self.areas:
            area.build_mlvl_dependencies(False)
            yield from area.dependencies_for()

        mlvl_deps = [self._raw.world_name_id, self._raw.world_save_info_id, self._raw.default_skybox_id]
        if self.asset_manager.target_game == Game.ECHOES:
            mlvl_deps.append(self._raw.dark_world_name_id)
        if self.asset_manager.target_game <= Game.CORRUPTION:
            mlvl_deps.append(self._raw.world_map_id)

        for dep in mlvl_deps:
            yield from self.asset_manager.get_dependencies_for_asset(dep)

    def __repr__(self) -> str:
        try:
            if self.asset_manager.target_game == Game.ECHOES:
                return f"{self.world_name} ({self.dark_world_name})"
            return self.world_name
        except UnknownAssetId:
            return super().__repr__()

    @property
    def areas(self) -> Iterator[Area]:
        area_layer_flags: list[list[bool]] = self._raw.area_layer_flags
        names: list[list[str]] = self._raw.area_layer_names

        for i, area in enumerate(self._raw.areas):
            yield Area(area, self.asset_manager, area_layer_flags[i], names[i], i, self)

    def get_area(self, asset_id: NameOrAssetId) -> Area:
        return next(area for area in self.areas if area.mrea_asset_id == self.asset_manager._resolve_asset_id(asset_id))

    def add_area(self, mrea_id: NameOrAssetId, name_id: NameOrAssetId, internal_name: str = "") -> Area:
        """
        :param mrea_id:
        :param name_id: TODO: will be changed into a string in the future
        :param internal_name:
        :return:
        """
        area_index = len(self._raw.areas)
        self._raw.areas.append(
            Container(
                area_name_id=self.asset_manager._resolve_asset_id(name_id),
                area_transform=[1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
                area_bounding_box=[-1.0, -1.0, -1.0, 1.0, 1.0, 1.0],
                area_mrea_id=self.asset_manager._resolve_asset_id(mrea_id),
                internal_area_id=area_index,
                attached_area_index=ListContainer(),
                dependencies=AreaDependencies([], []),
                docks=ListContainer(),
                module_dependencies=ListContainer(),
                internal_area_name=internal_name,
            )
        )
        self._raw.area_layer_flags.append([])
        self._raw.area_layer_names.append([])

        return Area(
            self._raw.areas[area_index],
            self.asset_manager,
            self._raw.area_layer_flags[area_index],
            self._raw.area_layer_names[area_index],
            area_index,
            self,
        )

    _name_strg_cached: Strg = None
    _dark_strg_cached: Strg = None

    @property
    def _name_strg(self) -> Strg:
        if self._name_strg_cached is None:
            self._name_strg_cached = self.asset_manager.get_asset(self._raw.world_name_id, type_hint=Strg)
        return self._name_strg_cached

    @property
    def _dark_strg(self) -> Strg:
        if self.asset_manager.target_game != Game.ECHOES:
            raise ValueError("Only Echoes has dark world names.")
        if self._dark_strg_cached is None:
            self._dark_strg_cached = self.asset_manager.get_asset(self._raw.dark_world_name_id, type_hint=Strg)
        return self._dark_strg_cached

    @property
    def world_name(self) -> str:
        return self._name_strg.strings[0]

    @world_name.setter
    def world_name(self, value: str):
        self._name_strg.set_string(0, value)

    @property
    def dark_world_name(self) -> str:
        return self._dark_strg.strings[0]

    @dark_world_name.setter
    def dark_world_name(self, value: str):
        self._dark_strg.set_string(0, value)

    @property
    def mapw(self) -> Mapw:
        if self._mapw is None:
            self._mapw = self.asset_manager.get_asset(self.raw.world_map_id, type_hint=Mapw)
        return self._mapw

    @property
    def savw(self) -> Savw:
        if self._savw is None:
            self._savw = self.asset_manager.get_asset(self.raw.world_save_info_id, type_hint=Savw)
        return self._savw
