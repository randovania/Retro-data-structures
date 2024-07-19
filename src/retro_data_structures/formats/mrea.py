"""
Wiki: https://wiki.axiodl.com/w/MREA_(Metroid_Prime_2)
"""

from __future__ import annotations

import copy
import dataclasses
import io
import itertools
import typing
from enum import IntEnum

import construct
from construct import Adapter, Aligned, If, Int32ub, PrefixedArray, Struct
from construct.core import (
    Array,
    Computed,
    Const,
    Enum,
    FixedSized,
    GreedyBytes,
    Int8ub,
)
from construct.lib.containers import Container, ListContainer

from retro_data_structures import game_check
from retro_data_structures.base_resource import AssetId, AssetType, BaseResource, Dependency
from retro_data_structures.common_types import AssetId32, FourCC, String, Transform4f
from retro_data_structures.compression import LZOCompressedBlock
from retro_data_structures.construct_extensions.alignment import PrefixedWithPaddingBefore
from retro_data_structures.construct_extensions.version import BeforeVersion, WithVersion
from retro_data_structures.data_section import DataSection
from retro_data_structures.exceptions import DependenciesHandledElsewhere, UnknownAssetId
from retro_data_structures.formats.area_collision import AreaCollision
from retro_data_structures.formats.arot import AROT
from retro_data_structures.formats.cmdl import dependencies_for_material_set
from retro_data_structures.formats.lights import Lights
from retro_data_structures.formats.script_layer import SCGN, SCLY, ScriptLayer, new_layer
from retro_data_structures.formats.strg import Strg
from retro_data_structures.formats.visi import VISI
from retro_data_structures.formats.world_geometry import lazy_world_geometry
from retro_data_structures.game_check import AssetIdCorrect, Game

if typing.TYPE_CHECKING:
    from collections.abc import Iterator

    from retro_data_structures.asset_manager import AssetManager
    from retro_data_structures.formats.mlvl import Mlvl
    from retro_data_structures.formats.script_object import (
        InstanceRef,
        ScriptInstance,
    )


class MREAVersion(IntEnum):
    PrimeKioskDemo = 0xC
    Prime = 0xF
    EchoesDemo = 0x15
    Echoes = 0x19
    CorruptionE3Prototype = 0x1D
    Corruption = 0x1E
    DonkeyKongCountryReturns = 0x20


class AreaDependencyAdapter(Adapter):
    def __init__(self, asset_id):
        super().__init__(
            Struct(
                Const(0, Int32ub),
                "dependencies"
                / PrefixedArray(
                    Int32ub,
                    Struct(
                        asset_id=asset_id,
                        asset_type=construct.Bytes(4),
                    ),
                ),
                "offsets" / PrefixedArray(Int32ub, Int32ub),
            ).compile()
        )

    def _decode(self, obj, context, path) -> AreaDependencies:
        layers = []
        for start, finish in zip(obj.offsets, obj.offsets[1:]):
            layers.append(
                [Dependency(dep.asset_type.decode("ascii"), dep.asset_id) for dep in obj.dependencies[start:finish]]
            )

        non_layer = [
            Dependency(dep.asset_type.decode("ascii"), dep.asset_id) for dep in obj.dependencies[obj.offsets[-1] :]
        ]

        return AreaDependencies(layers, non_layer)

    def _encode(self, obj: AreaDependencies, context, path):
        layer_deps = list(obj.layers)
        layer_deps.append(obj.non_layer)

        deps = []
        offsets = []
        for layer_dep in layer_deps:
            offsets.append(len(deps))
            deps.extend(
                Container(asset_type=dep.type.encode("ascii"), asset_id=dep.id)
                for dep in layer_dep
                if not dep.exclude_for_mlvl
            )

        return {"dependencies": deps, "offsets": offsets}


class AreaModuleDependencyAdapter(Adapter):
    def __init__(self):
        super().__init__(
            Struct(
                rel_module=PrefixedArray(Int32ub, String),
                rel_offset=PrefixedArray(Int32ub, Int32ub),
            )
        )

    def _decode(self, obj, context, path) -> list[list[str]]:
        offset_iter = iter(obj.rel_offset)
        return [obj.rel_module[start:finish] for start, finish in zip(offset_iter, offset_iter)]

    def _encode(self, obj: list[list[str]], context, path):
        offset = 0
        offsets = []
        for layer in obj:
            offsets.append(offset)
            offset += len(layer)
            offsets.append(offset)

        return {"rel_module": list(itertools.chain(*obj)), "rel_offset": offsets}


_all_categories = [
    "geometry_section",
    "unknown_section_2",
    "script_layers_section",
    "generated_script_objects_section",
    "collision_section",
    "unknown_section_1",
    "lights_section",
    "visibility_tree_section",
    "path_section",
    "area_octree_section",
    "portal_area_section",
    "static_geometry_map_section",
]

_CATEGORY_ENCODINGS = {
    "geometry_section": lazy_world_geometry(),
    "script_layers_section": SCLY,
    "generated_script_objects_section": SCGN,
    "area_octree_section": AROT,
    "collision_section": AreaCollision,
    "lights_section": Lights,
    "visibility_tree_section": VISI,
    "path_section": AssetIdCorrect,
    "portal_area_section": AssetIdCorrect,
    "static_geometry_map_section": AssetIdCorrect,
    "unknown_section_1": Struct(magic=If(game_check.is_prime3, Const("LLTE", FourCC)), data=Const(1, Int32ub)),
    "unknown_section_2": Struct(
        unk1=PrefixedArray(Int32ub, Int32ub),
        # TODO: rebuild according to surface group count
        unk2=PrefixedArray(Int32ub, Enum(Int8ub, ON=0xFF, OFF=0x00)),
    ),
}

MREAHeader = Aligned(
    32,
    Struct(
        "magic" / Const(0xDEADBEEF, Int32ub),
        "version" / Enum(Int32ub, MREAVersion),
        # Matrix that represents the area's transform from the origin.
        # Most area data is pre-transformed, so this matrix is only used occasionally.
        "area_transform" / Transform4f,
        # Number of world models in this area.
        "world_model_count" / Int32ub,
        # Number of script layers in this area.
        "script_layer_count" / WithVersion(MREAVersion.Echoes, Int32ub),
        # Number of data sections in the file.
        "data_section_count" / Int32ub,
        # Section index for world geometry data. Always 0; starts on materials.
        "geometry_section" / Int32ub,
        # Section index for script layer data.
        "script_layers_section" / Int32ub,
        # Section index for generated script object data.
        "generated_script_objects_section" / WithVersion(MREAVersion.Echoes, Int32ub),
        # Section index for collision data.
        "collision_section" / Int32ub,
        # Section index for first unknown section.
        "unknown_section_1" / Int32ub,
        # Section index for light data.
        "lights_section" / Int32ub,
        # Section index for visibility tree data.
        "visibility_tree_section" / Int32ub,
        # Section index for path data.
        "path_section" / Int32ub,
        # Section index for area octree data.
        "area_octree_section" / BeforeVersion(MREAVersion.EchoesDemo, Int32ub),
        # Section index for second unknown section.
        "unknown_section_2" / WithVersion(MREAVersion.Echoes, Int32ub),
        # Section index for portal area data.
        "portal_area_section" / WithVersion(MREAVersion.Echoes, Int32ub),
        # Section index for static geometry map data.
        "static_geometry_map_section" / WithVersion(MREAVersion.Echoes, Int32ub),
        # Number of compressed data blocks in the file.
        "compressed_block_count" / WithVersion(MREAVersion.Echoes, Int32ub),
    ),
)

CompressedBlockHeader = Struct(
    buffer_size=Int32ub,
    uncompressed_size=Int32ub,
    compressed_size=Int32ub,
    data_section_count=Int32ub,
)


def _get_compressed_block_size(header):
    if not header.compressed_size:
        return header.uncompressed_size
    return header.compressed_size + (-header.compressed_size % 32)


def _get_compressed_block_subcon(compressed_size, uncompressed_size):
    if compressed_size:
        return PrefixedWithPaddingBefore(Computed(compressed_size), LZOCompressedBlock(uncompressed_size))
    return DataSection(GreedyBytes, size=lambda: Computed(uncompressed_size))


def _decode_category(category: list[bytes], subcon: construct.Construct, context, path):
    result = ListContainer()

    for section in category:
        if len(section) > 0:
            data = subcon._parse(io.BytesIO(section), context, path)
        else:
            data = None

        result.append(data)

    return result


def _encode_category(category: list, subcon: construct.Construct, context, path) -> list[bytes]:
    result = ListContainer()

    for section in category:
        if section is not None:
            stream = io.BytesIO()
            if isinstance(section, bytes) or subcon is None:
                this_subcon = GreedyBytes
            else:
                this_subcon = subcon

            Aligned(32, this_subcon)._build(section, stream, context, path)
            data = stream.getvalue()
        else:
            data = b""

        result.append(data)

    return result


class MREAConstruct(construct.Construct):
    def _aligned_parse(self, conn: construct.Construct, stream, context, path):
        return Aligned(32, conn)._parsereport(stream, context, path)

    def _decode_compressed_blocks(self, mrea_header, data_section_sizes, stream, context, path) -> list[bytes]:
        compressed_block_headers = self._aligned_parse(
            Array(mrea_header.compressed_block_count, CompressedBlockHeader), stream, context, path
        )

        # Read compressed blocks from stream
        compressed_blocks = construct.ListContainer(
            self._aligned_parse(
                FixedSized(_get_compressed_block_size(header), GreedyBytes),
                stream,
                context,
                path,
            )
            for header in compressed_block_headers
        )

        # Decompress blocks into the data sections
        data_sections = ListContainer()
        for compressed_header, compressed_block in zip(compressed_block_headers, compressed_blocks):
            subcon = _get_compressed_block_subcon(
                compressed_header.compressed_size, compressed_header.uncompressed_size
            )
            decompressed_block = subcon._parsereport(io.BytesIO(compressed_block), context, path)
            if len(decompressed_block) != compressed_header.uncompressed_size:
                raise construct.ConstructError(
                    f"Expected {compressed_header.uncompressed_size} bytes, got {len(decompressed_block)}",
                    path,
                )
            offset = 0

            for i in range(compressed_header.data_section_count):
                section_size = data_section_sizes[len(data_sections)]
                data = decompressed_block[offset : offset + section_size]
                data_sections.append(data)
                offset += section_size

        return data_sections

    def _parse(self, stream, context, path):
        mrea_header = MREAHeader._parsereport(stream, context, path)
        data_section_sizes = self._aligned_parse(Array(mrea_header.data_section_count, Int32ub), stream, context, path)

        if mrea_header.compressed_block_count is not None:
            data_sections = self._decode_compressed_blocks(mrea_header, data_section_sizes, stream, context, path)
        else:
            data_sections = Array(
                mrea_header.data_section_count,
                Aligned(32, FixedSized(lambda ctx: data_section_sizes[ctx._index], GreedyBytes)),
            )._parsereport(stream, context, path)

        # Split data sections into the named sections
        categories = [
            {"label": label, "value": mrea_header[label]} for label in _all_categories if mrea_header[label] is not None
        ]
        categories.sort(key=lambda c: c["value"])

        sections = Container()
        for i, c in enumerate(categories):
            start = c["value"]
            end = None
            if i < len(categories) - 1:
                end = categories[i + 1]["value"]
            sections[c["label"]] = data_sections[start:end]

        return Container(
            version=mrea_header.version,
            area_transform=mrea_header.area_transform,
            world_model_count=mrea_header.world_model_count,
            raw_sections=sections,
            sections=construct.Container(),
        )

    def _encode_compressed_blocks(
        self, data_sections: list[bytes], category_starts: dict[str, int | None], context, path
    ):
        def _start_new_group(group_size, section_size, curr_label, prev_label):  # noqa: PLR0911
            if group_size == 0:
                return False, ""

            if group_size + section_size > 0x20000:
                return True, "Next section too big."

            if curr_label == "script_layers_section":
                return True, "New SCLY section."

            elif prev_label == "script_layers_section":
                return True, "Previous SCLY completed."

            if curr_label == "generated_script_objects_section":
                return True, "New SCGN section."

            elif prev_label == "generated_script_objects_section":
                return True, "Previous SCGN completed."

            return False, ""

        compressed_blocks = ListContainer()
        filtered_starts = [(cat, start) for cat, start in category_starts.items() if start is not None]
        filtered_starts.sort(key=lambda it: it[1])
        category_starts = dict(filtered_starts)

        current_group_size = 0
        current_group = []
        previous_label = ""

        def add_group(r):
            nonlocal current_group_size, current_group
            # print(f"Group complete! {r} Group size: {current_group_size}")

            # The padding is not included in the block's uncompressed size
            merged_and_padded_group = b"".join(
                item.ljust(len(item) + (-len(item) % 32), b"\x00") for item in current_group
            )
            header = Container(
                buffer_size=current_group_size,
                uncompressed_size=current_group_size,
                compressed_size=0,
                data_section_count=len(current_group),
            )

            substream = io.BytesIO()
            LZOCompressedBlock(header.uncompressed_size)._build(merged_and_padded_group, substream, context, path)
            data = substream.getvalue()
            compressed_size = len(data)
            compressed_pad = (32 - (compressed_size % 32)) & 0x1F
            if compressed_size + compressed_pad < header.uncompressed_size:
                header.compressed_size = compressed_size
                header.buffer_size += 0x120
            else:
                data = merged_and_padded_group

            compressed_blocks.append(
                Container(
                    header=header,
                    data=data,
                )
            )
            current_group = ListContainer()
            current_group_size = 0

        for i, section in enumerate(data_sections):
            all_garbage = [cat for cat, start in category_starts.items() if i >= start]
            cat_label = all_garbage[-1]

            start_new, reason = _start_new_group(current_group_size, len(section), previous_label, cat_label)
            if start_new:
                add_group(reason)

            current_group.append(section)
            current_group_size += len(section)

            previous_label = cat_label

        add_group("Final group.")
        return compressed_blocks

    def _build(self, obj: Container, stream, context, path):
        mrea_header = Container()

        raw_sections = copy.copy(obj.raw_sections)

        # Encode each category
        for category, values in obj.sections.items():
            raw_sections[category] = _encode_category(
                values, _CATEGORY_ENCODINGS.get(category), context, f"{path} -> {category}"
            )

        # Combine all sections into the data sections array
        data_sections = ListContainer()

        for category in _all_categories:
            if category in raw_sections:
                mrea_header[category] = len(data_sections)
                data_sections.extend(raw_sections[category])
            else:
                mrea_header[category] = None

        # Compress the data sections
        if int(obj.version) >= MREAVersion.Echoes.value:
            compressed_blocks = self._encode_compressed_blocks(data_sections, mrea_header, context, path)
            mrea_header.compressed_block_count = len(compressed_blocks)
        else:
            compressed_blocks = None
            raise RuntimeError("Not implemented yet")

        mrea_header.version = obj.version
        mrea_header.area_transform = obj.area_transform
        mrea_header.world_model_count = obj.world_model_count
        mrea_header.script_layer_count = len(raw_sections.script_layers_section)
        mrea_header.data_section_count = len(data_sections)

        MREAHeader._build(mrea_header, stream, context, path)
        Aligned(32, Array(mrea_header.data_section_count, Int32ub))._build(
            [len(section) for section in data_sections],
            stream,
            context,
            path,
        )
        if compressed_blocks is not None:
            Aligned(32, Array(mrea_header.compressed_block_count, CompressedBlockHeader))._build(
                [block.header for block in compressed_blocks],
                stream,
                context,
                path,
            )
            for compressed_block in compressed_blocks:
                block_header = compressed_block.header
                if block_header.compressed_size:
                    subcon = PrefixedWithPaddingBefore(Computed(block_header.compressed_size), GreedyBytes)
                else:
                    subcon = DataSection(GreedyBytes, size=lambda: Computed(block_header.uncompressed_size))
                subcon._build(compressed_block.data, stream, context, path)
        else:
            # TODO
            pass


MREA = MREAConstruct()


class Mrea(BaseResource):
    _script_layer_helpers: dict[int, ScriptLayer] | None = None

    @classmethod
    def resource_type(cls) -> AssetType:
        return "MREA"

    @classmethod
    def construct_class(cls, target_game: Game) -> construct.Construct:
        return MREA

    def dependencies_for(self) -> typing.Iterator[Dependency]:
        raise DependenciesHandledElsewhere

    def _ensure_decoded_section(self, section_name: str, lazy_load: bool = False):
        if section_name not in self._raw.sections:
            context = Container(target_game=self.target_game)
            context._parsing = True
            context._building = False
            context._sizing = False
            context._params = context

            self._raw.sections[section_name] = _decode_category(
                self._raw.raw_sections[section_name],
                GreedyBytes if lazy_load else _CATEGORY_ENCODINGS[section_name],
                context,
                "",
            )

    def get_section(self, section_name: str, lazy_load: bool = False):
        self._ensure_decoded_section(section_name, lazy_load)
        return self._raw.sections[section_name]

    def get_raw_section(self, section_name: str) -> list[bytes]:
        return list(self._raw.raw_sections[section_name])

    def get_geometry(self):
        return self.get_section("geometry_section")

    def get_portal_area(self) -> AssetId:
        return self.get_section("portal_area_section")[0]

    def get_static_geometry_map(self) -> AssetId:
        return self.get_section("static_geometry_map_section")[0]

    def get_path(self) -> AssetId:
        return self.get_section("path_section")[0]

    def build(self) -> bytes:
        for i, section in (self._script_layer_helpers or {}).items():
            if section.is_modified():
                self._raw.sections.script_layers_section[i] = section._raw
        return super().build()

    @property
    def script_layers(self) -> Iterator[ScriptLayer]:
        self._ensure_decoded_section("script_layers_section", lazy_load=self.target_game != Game.PRIME)

        if self.target_game == Game.PRIME:
            section = self._raw.sections.script_layers_section[0]
            for i, layer in enumerate(section.layers):
                yield ScriptLayer(layer, i, self.target_game)
        else:
            if self._script_layer_helpers is None:
                self._script_layer_helpers = {}

            for i, section in enumerate(self._raw.sections.script_layers_section):
                if i not in self._script_layer_helpers:
                    self._script_layer_helpers[i] = ScriptLayer(
                        _CATEGORY_ENCODINGS["script_layers_section"].parse(section, target_game=self.target_game),
                        i,
                        self.target_game,
                    )

            yield from self._script_layer_helpers.values()

    _generated_objects_layer: ScriptLayer | None = None

    @property
    def generated_objects_layer(self) -> ScriptLayer:
        assert self.target_game >= Game.ECHOES
        if self._generated_objects_layer is None:
            self._generated_objects_layer = ScriptLayer(
                self.get_section("generated_script_objects_section")[0], None, self.target_game
            )
        return self._generated_objects_layer


@dataclasses.dataclass(frozen=True)
class AreaDependencies:
    layers: list[list[Dependency]]
    non_layer: list[Dependency]

    @property
    def all_dependencies(self) -> Iterator[Dependency]:
        yield from self.non_layer
        for layer in self.layers:
            yield from layer

    def __eq__(self, __value: object) -> bool:
        if not isinstance(__value, AreaDependencies):
            return False
        for self_layer, other_layer in zip(self.layers, __value.layers):
            if set(self_layer) != set(other_layer):
                return False
        return set(self.non_layer) == set(__value.non_layer)


class Area:
    _flags: list[bool]
    _layer_names: list[str]
    _index: int

    _mrea: Mrea = None
    _strg: Strg = None

    # FIXME: since the whole Mlvl is now being passed, this function can have the other arguments removed
    # FIXME: also every time i've tried to do this it breaks mlvl dependencies. good luck!
    def __init__(
        self,
        raw: Container,
        asset_manager: AssetManager,
        flags: list[bool],
        names: list[str],
        index: int,
        parent_mlvl: Mlvl,
    ):
        self._raw = raw
        self.asset_manager = asset_manager
        self._flags = flags
        self._layer_names = names
        self._index = index
        self._parent_mlvl = parent_mlvl

    @property
    def id(self) -> int:
        return self._raw.internal_area_id

    @property
    def index(self) -> int:
        return self._index

    @property
    def name(self) -> str:
        try:
            return self.strg.strings[0]
        except UnknownAssetId:
            return "!!" + self.internal_name

    @name.setter
    def name(self, value):
        self.strg.set_string(0, value)

    @property
    def internal_name(self) -> str:
        return self._raw.get("internal_area_name", "Unknown")

    @property
    def strg(self) -> Strg:
        if self._strg is None:
            self._strg = self.asset_manager.get_asset(self._raw.area_name_id, type_hint=Strg)
        return self._strg

    @property
    def mrea(self) -> Mrea:
        if self._mrea is None:
            self._mrea = self.asset_manager.get_asset(self.mrea_asset_id, type_hint=Mrea)
        return self._mrea

    @property
    def mrea_asset_id(self) -> int:
        return self._raw.area_mrea_id

    @property
    def layers(self) -> Iterator[ScriptLayer]:
        for layer in self.mrea.script_layers:
            yield layer.with_parent(self)

    @property
    def generated_objects_layer(self) -> ScriptLayer:
        return self.mrea.generated_objects_layer.with_parent(self)

    @property
    def all_layers(self) -> Iterator[ScriptLayer]:
        yield from self.layers
        yield self.generated_objects_layer

    def get_layer(self, name: str) -> ScriptLayer:
        return next(layer for layer in self.layers if layer.name == name)

    def add_layer(self, name: str, active: bool = True) -> ScriptLayer:
        # FIXME
        index = len(self._layer_names)
        self._layer_names.append(name)
        self._flags.append(active)
        raw = new_layer(index, self.asset_manager.target_game)
        self.mrea.get_section("script_layer_section").append(raw)
        return self.get_layer(name)

    @property
    def next_instance_id(self) -> int:
        ids = {instance.id.instance for instance in self.all_instances}
        return next(i for i in itertools.count() if i not in ids)

    @property
    def all_instances(self) -> Iterator[ScriptInstance]:
        for layer in self.all_layers:
            yield from layer.instances

    def get_instance(self, ref: InstanceRef) -> ScriptInstance:
        for layer in self.all_layers:
            try:
                return layer.get_instance(ref)
            except KeyError:
                pass
        raise KeyError(ref)

    def remove_instance(self, instance: InstanceRef):
        for layer in self.all_layers:
            try:
                layer.remove_instance(instance)
            except KeyError:
                pass
            else:
                return
        raise KeyError(instance)

    def _raw_connect_to(self, source_dock_number: int, target_area: Area, target_dock_number: int):
        source_dock = self._raw.docks[source_dock_number]
        assert len(source_dock.connecting_dock) == 1, "Only docks with one connection supported"
        source_dock.connecting_dock[0].area_index = target_area._index
        source_dock.connecting_dock[0].dock_index = target_dock_number

        attached_area_index = []
        for docks in self._raw.docks:
            for c in docks.connecting_dock:
                if c.area_index not in attached_area_index:
                    attached_area_index.append(c.area_index)
        self._raw.attached_area_index = construct.ListContainer(attached_area_index)

    def connect_dock_to(self, source_dock_number: int, target_area: Area, target_dock_number: int):
        self._raw_connect_to(source_dock_number, target_area, target_dock_number)
        target_area._raw_connect_to(target_dock_number, self, source_dock_number)

    def build_non_layer_dependencies(self) -> typing.Iterator[Dependency]:
        if self.asset_manager.target_game <= Game.ECHOES:
            geometry_section = self.mrea.get_raw_section("geometry_section")
            if geometry_section:
                for asset_id in PrefixedArray(Int32ub, AssetId32).parse(geometry_section[0]):
                    yield from self.asset_manager.get_dependencies_for_asset(asset_id)
        else:
            geometry = self.mrea.get_geometry()
            if geometry is not None:
                yield from dependencies_for_material_set(geometry[0].materials, self.asset_manager)

        valid_asset = self.asset_manager.target_game.is_valid_asset_id
        if valid_asset(portal_area := self.mrea.get_portal_area()):
            yield Dependency("PTLA", portal_area)
        if valid_asset(static_geometry_map := self.mrea.get_static_geometry_map()):
            yield Dependency("EGMC", static_geometry_map)
        if valid_asset(path := self.mrea.get_path()):
            yield Dependency("PATH", path)

    def build_scgn_dependencies(self, layer_deps: list[list[Dependency]], only_modified: bool = False):
        layer_deps = list(layer_deps)

        layers = list(self.layers)
        for instance in self.generated_objects_layer.instances:
            inst_layer = instance.id.layer
            if not only_modified or layers[inst_layer].is_modified:
                layer_deps[inst_layer].extend(instance.mlvl_dependencies_for(self.asset_manager))

        return [list(dict.fromkeys(deps)) for deps in layer_deps]

    def build_mlvl_dependencies(self, only_modified: bool = False):
        layer_deps = [
            list(
                layer.build_mlvl_dependencies(self.asset_manager)
                if (not only_modified) or layer.is_modified()
                else layer.dependencies
            )
            for layer in self.layers
        ]

        layer_deps = self.build_scgn_dependencies(layer_deps, only_modified)

        if self.mrea_asset_id in _hardcoded_dependencies:
            for layer_name, missing in _hardcoded_dependencies[self.mrea_asset_id].items():
                if layer_name == "!!non_layer!!":
                    continue

                layer = self.get_layer(layer_name)
                if only_modified and not layer.is_modified():
                    continue

                layer_deps[layer.index].extend(missing)

        if only_modified:
            # assume we never modify these
            non_layer = self.dependencies.non_layer
        else:
            non_layer = list(self.build_non_layer_dependencies())
            if "!!non_layer!!" in _hardcoded_dependencies.get(self.mrea_asset_id, {}):
                non_layer.extend(_hardcoded_dependencies[self.mrea_asset_id]["!!non_layer!!"])

        self.dependencies = AreaDependencies(layer_deps, non_layer)

    def dependencies_for(self):
        yield from self.dependencies.all_dependencies
        yield from self.asset_manager.get_dependencies_for_asset(self.mrea_asset_id)
        yield from self.asset_manager.get_dependencies_for_asset(self._raw.area_name_id)

    def ensure_dependencies_in_paks(self):
        asset_manager = self._parent_mlvl.asset_manager
        paks = list(asset_manager.find_paks(self.mrea_asset_id))

        for dep in self.dependencies_for():
            for pak in paks:
                asset_manager.ensure_present(pak, dep.id)

    def build_module_dependencies(self, only_modified: bool = False):
        if self._parent_mlvl.asset_manager.target_game == Game.PRIME:
            return
        layers = list(self.layers)
        layer_rels = [
            list(
                layer.build_module_dependencies()
                if (not only_modified) or layer.is_modified()
                else layer.module_dependencies
            )
            for layer in layers
        ]

        for instance in self.generated_objects_layer.instances:
            layer = instance.id.layer
            if (not only_modified) or layers[layer].is_modified():
                layer_rels[layer].extend(instance.type.modules())

        layer_rels = [list(dict.fromkeys(layer)) for layer in layer_rels]
        self.module_dependencies = layer_rels

    @property
    def dependencies(self) -> AreaDependencies:
        return self._raw.dependencies

    @dependencies.setter
    def dependencies(self, new: AreaDependencies):
        self._raw.dependencies = new

    @property
    def dependencies_by_layer(self) -> dict[str, list[Dependency]]:
        deps = {layer.name: [dep for dep in layer.dependencies if not dep.exclude_for_mlvl] for layer in self.layers}
        deps["!!non_layer!!"] = list(self.dependencies.non_layer)
        return deps

    @property
    def module_dependencies(self) -> list[list[str]]:
        return self._raw.module_dependencies

    @module_dependencies.setter
    def module_dependencies(self, new: list[list[str]]):
        self._raw.module_dependencies = new

    @property
    def module_dependencies_by_layer(self) -> dict[str, list[str]]:
        return {layer.name: list(layer.module_dependencies) for layer in self.layers}

    def update_all_dependencies(self, only_modified: bool = False):
        self.build_mlvl_dependencies(only_modified)
        self.build_module_dependencies(only_modified)
        self.ensure_dependencies_in_paks()


_hardcoded_dependencies: dict[int, dict[str, list[Dependency]]] = {
    0xD7C3B839: {
        # Sanctum
        "Default": [Dependency("TXTR", 0xD5B9E5D1)],
        "Emperor Ing Stage 1": [Dependency("TXTR", 0x52C7D438)],
        "Emperor Ing Stage 3": [Dependency("TXTR", 0xD5B9E5D1)],
        "Emperor Ing Stage 1 Intro Cine": [Dependency("TXTR", 0x52C7D438)],
        "Emperor Ing Stage 3 Death Cine": [Dependency("TXTR", 0xD5B9E5D1)],
    },
    0xA92F00B3: {
        # Hive Temple
        "CliffsideBoss": [
            Dependency("TXTR", 0x24149E16),
            Dependency("TXTR", 0xBDB8A88A),
            Dependency("FSM2", 0x3D31822B),
        ]
    },
    0xC0113CE8: {
        # Dynamo Works
        "3rd Pass": [Dependency("RULE", 0x393CA543)]
    },
    0x5571E89E: {
        # Hall of Combat Mastery
        "2nd Pass Enemies": [Dependency("RULE", 0x393CA543)]
    },
    0x7B94B06B: {
        # Hive Portal Chamber
        "1st Pass": [Dependency("RULE", 0x393CA543)],
        "2nd Pass": [Dependency("RULE", 0x393CA543)],
    },
    0xF8DBC03D: {
        # Hive Reactor
        "2nd Pass": [Dependency("RULE", 0x393CA543)]
    },
    0xB666B655: {
        # Reactor Access
        "2nd Pass": [Dependency("RULE", 0x393CA543)]
    },
    0xE79AAFAE: {
        # Transport A Access
        "2nd Pass": [Dependency("RULE", 0x393CA543)]
    },
    0xFEB7BD27: {
        # Transport B Access
        "Default": [Dependency("RULE", 0x393CA543)]
    },
    0x89D246FD: {
        # Portal Access
        "Default": [Dependency("RULE", 0x393CA543)]
    },
    0x0253782D: {
        # Dark Forgotten Bridge
        "Default": [Dependency("RULE", 0x393CA543)]
    },
    0x09DECF21: {
        # Forgotten Bridge
        "Default": [Dependency("RULE", 0x393CA543)]
    },
    0x629790F4: {
        # Sacrificial Chamber
        "1st Pass": [Dependency("RULE", 0x393CA543)]
    },
    0xBBE4B3AE: {
        # Dungeon
        "Default": [Dependency("TXTR", 0xE252E7F6)]
    },
    0x2BCD44A7: {
        # Portal Terminal
        "Default": [Dependency("TXTR", 0xB6FA5023)]
    },
    0xC68B5B51: {
        # Transport to Sanctuary Fortress
        "!!non_layer!!": [Dependency("TXTR", 0x75A219A8)]
    },
    0x625A2692: {
        # Temple Transport Access
        "!!non_layer!!": [Dependency("TXTR", 0x581C56EA)]
    },
    0x96F4CA1E: {
        # Minigyro Chamber
        "Default": [Dependency("TXTR", 0xAC080DFB)]
    },
    0x5BBF334F: {
        # Staging Area
        "!!non_layer!!": [Dependency("TXTR", 0x738FEB19)]
    },
}
