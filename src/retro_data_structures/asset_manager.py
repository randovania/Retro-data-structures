from __future__ import annotations

import collections
import fnmatch
import json
import logging
import typing
from collections import defaultdict

from retro_data_structures import formats
from retro_data_structures.base_resource import (
    AssetId,
    AssetType,
    BaseResource,
    Dependency,
    NameOrAssetId,
    RawResource,
    Resource,
    resolve_asset_id,
)
from retro_data_structures.disc.game_disc import GameDisc
from retro_data_structures.exceptions import DependenciesHandledElsewhere, UnknownAssetId
from retro_data_structures.formats import Dgrp, dependency_cheating
from retro_data_structures.formats.audio_group import Agsc, Atbl
from retro_data_structures.formats.pak import Pak

if typing.TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import construct

    from retro_data_structures.formats.ancs import Ancs
    from retro_data_structures.game_check import Game

T = typing.TypeVar("T", bound=BaseResource)
logger = logging.getLogger(__name__)


class FileProvider:
    def is_file(self, name: str) -> bool:
        raise NotImplementedError

    def rglob(self, pattern: str) -> Iterator[str]:
        raise NotImplementedError

    def open_binary(self, name: str) -> typing.BinaryIO:
        raise NotImplementedError

    def read_binary(self, name: str) -> bytes:
        raise NotImplementedError

    def get_dol(self) -> bytes:
        raise NotImplementedError

    def get_file_list(self) -> list[str]:
        raise NotImplementedError


class PathFileProvider(FileProvider):
    def __init__(self, root: Path):
        if not root.is_dir():
            raise FileNotFoundError(f"{root} is not a directory")
        self.root = root
        self.file_root = root.joinpath("files")

    def __repr__(self):
        return f"<PathFileProvider {self.root}>"

    def is_file(self, name: str) -> bool:
        return self.file_root.joinpath(name).is_file()

    def rglob(self, name: str) -> Iterator[str]:
        for it in self.file_root.rglob(name):
            if it.is_file():
                yield it.relative_to(self.file_root).as_posix()

    def open_binary(self, name: str) -> typing.BinaryIO:
        return self.file_root.joinpath(name).open("rb")

    def read_binary(self, name: str) -> bytes:
        with self.open_binary(name) as f:
            return f.read()

    def get_dol(self) -> bytes:
        return self.root.joinpath("sys/main.dol").read_bytes()

    def get_file_list(self) -> list[str]:
        return list(self.rglob("*"))


class IsoFileProvider(FileProvider):
    game_disc: GameDisc | None

    def __init__(self, iso_path: Path):
        self.iso_path = iso_path

        self.game_disc = GameDisc.parse(iso_path)
        self.all_files = self.game_disc.files()

    def __repr__(self):
        return f"<IsoFileProvider {self.iso_path}>"

    def is_file(self, name: str) -> bool:
        return name in self.all_files

    def rglob(self, pattern: str) -> Iterator[str]:
        for it in self.all_files:
            if fnmatch.fnmatch(it, pattern):
                yield it

    def open_binary(self, name: str):
        return self.game_disc.open_binary(name)

    def read_binary(self, name: str) -> bytes:
        return self.game_disc.read_binary(name)

    def get_dol(self) -> bytes:
        return self.game_disc.get_dol()

    def get_file_list(self) -> list[str]:
        return list(self.all_files)


class AssetManager:
    """
    Manages efficiently reading all PAKs in the game and writing out modifications to a new path.

    _files_for_asset_id: mapping of asset id to all paks it can be found at
    _ensured_asset_ids: mapping of pak name to assets we'll copy into it when saving
    _modified_resources: mapping of asset id to raw resources. When saving, these asset ids are replaced
    """

    headers: dict[str, construct.Container]
    _paks_for_asset_id: dict[AssetId, set[str]]
    _types_for_asset_id: dict[AssetId, AssetType]
    _ensured_asset_ids: dict[str, set[AssetId]]
    _modified_resources: dict[AssetId, RawResource | None]
    _in_memory_paks: dict[str, Pak]
    _custom_asset_ids: dict[str, AssetId]
    _audio_group_dependency: tuple[Dgrp, ...] | None = None

    _cached_dependencies: dict[AssetId, tuple[Dependency, ...]]
    _cached_ancs_per_char_dependencies: defaultdict[AssetId, dict[int, tuple[Dependency, ...]]]
    _sound_id_to_agsc: dict[int, AssetId | None] | None = None

    def __init__(self, provider: FileProvider, target_game: Game):
        self.provider = provider
        self.target_game = target_game
        self._modified_resources = {}
        self._in_memory_paks = {}
        self._next_generated_id = 0xFFFF0000

        self._update_headers()

        self._cached_dependencies = {}
        self._cached_ancs_per_char_dependencies = defaultdict(dict)

    def _resolve_asset_id(self, value: NameOrAssetId) -> AssetId:
        if str(value) in self._custom_asset_ids:
            return self._custom_asset_ids[str(value)]
        return resolve_asset_id(self.target_game, value)

    def _update_headers(self):
        self._ensured_asset_ids = {}
        self._paks_for_asset_id = collections.defaultdict(set)
        self._types_for_asset_id = {}

        self._custom_asset_ids = {}
        if self.provider.is_file("custom_names.json"):
            custom_names_text = self.provider.read_binary("custom_names.json").decode("utf-8")

            self._custom_asset_ids.update(dict(json.loads(custom_names_text).items()))

        self.all_paks = list(self.provider.rglob("*.pak"))

        for name in self.all_paks:
            with self.provider.open_binary(name) as f:
                pak_no_data = Pak.header_for_game(self.target_game).parse_stream(f, target_game=self.target_game)

            self._ensured_asset_ids[name] = set()
            for entry in pak_no_data.resources:
                self._paks_for_asset_id[entry.asset_id].add(name)
                self._types_for_asset_id[entry.asset_id] = entry.asset_type

    def all_asset_ids(self) -> Iterator[AssetId]:
        """
        Returns an iterator of all asset ids in the available paks.
        """
        yield from self._paks_for_asset_id.keys()

    def find_paks(self, asset_id: NameOrAssetId) -> Iterator[str]:
        original_name = asset_id
        asset_id = self._resolve_asset_id(asset_id)
        try:
            yield from self._paks_for_asset_id[asset_id]
        except KeyError:
            raise UnknownAssetId(asset_id, original_name) from None

    def does_asset_exists(self, asset_id: NameOrAssetId) -> bool:
        """
        Checks if a given asset id exists.
        """
        asset_id = self._resolve_asset_id(asset_id)

        if asset_id in self._modified_resources:
            return self._modified_resources[asset_id] is not None

        return asset_id in self._paks_for_asset_id

    def get_asset_type(self, asset_id: NameOrAssetId) -> AssetType:
        """
        Gets the type that is associated with the given asset name/id in the pak headers.
        :param asset_id:
        :return:
        """
        original_name = asset_id
        asset_id = self._resolve_asset_id(asset_id)

        if asset_id in self._modified_resources:
            result = self._modified_resources[asset_id]
            if result is None:
                raise ValueError(f"Deleted asset_id: {original_name}")
            else:
                return result.type

        try:
            return self._types_for_asset_id[asset_id]
        except KeyError:
            raise UnknownAssetId(asset_id, original_name) from None

    def get_raw_asset(self, asset_id: NameOrAssetId) -> RawResource:
        """
        Gets the bytes data for the given asset name/id, optionally restricting from which pak.
        :raises ValueError if the asset doesn't exist.
        """
        original_name = asset_id
        asset_id = self._resolve_asset_id(asset_id)

        if asset_id in self._modified_resources:
            result = self._modified_resources[asset_id]
            if result is None:
                raise ValueError(f"Deleted asset_id: {original_name}")
            else:
                return result

        try:
            for pak_name in self._paks_for_asset_id[asset_id]:
                pak = self.get_pak(pak_name)
                result = pak.get_asset(asset_id)
                if result is not None:
                    return result
        except KeyError:
            raise UnknownAssetId(asset_id, original_name) from None

    def get_asset_format(self, asset_id: NameOrAssetId) -> type[BaseResource]:
        asset_type = self.get_asset_type(asset_id)
        return formats.resource_type_for(asset_type)

    def get_parsed_asset(self, asset_id: NameOrAssetId, *, type_hint: type[T] = BaseResource) -> T:
        """
        Gets the resource with the given name and decodes it based on the extension.
        """
        format_class = self.get_asset_format(asset_id)
        if type_hint is not BaseResource and type_hint != format_class:
            raise ValueError(f"type_hint was {type_hint}, pak listed {format_class}")

        return format_class.parse(self.get_raw_asset(asset_id).data, target_game=self.target_game, asset_manager=self)

    def get_asset(self, path: NameOrAssetId, type_hint: type[T] = BaseResource) -> T:
        """
        Wrapper for get_parsed_asset. Override in subclasses for additional behavior such as automatic saving.
        """
        return self.get_parsed_asset(path, type_hint=type_hint)

    def generate_asset_id(self):
        result = self._next_generated_id
        while self.does_asset_exists(result):
            result += 1

        self._next_generated_id = result + 1
        return result

    def register_custom_asset_name(self, name: str, asset_id: AssetId):
        if self.does_asset_exists(asset_id):
            raise ValueError(f"{asset_id} ({name}) already exists")

        if name in self._custom_asset_ids and self._custom_asset_ids[name] != asset_id:
            raise ValueError(f"{name} already exists")

        self._custom_asset_ids[name] = asset_id

    def get_custom_asset(self, name: str) -> AssetId | None:
        return self._custom_asset_ids.get(name)

    def add_new_asset(self, name: str, new_data: Resource, in_paks: typing.Iterable[str] = ()) -> AssetId:
        """
        Adds an asset that doesn't already exist.
        :return: Asset id of the new asset.
        """
        asset_id = self._resolve_asset_id(name)

        if self.does_asset_exists(asset_id):
            raise ValueError(f"{name} already exists")

        in_paks = list(in_paks)
        files_set = set()

        self._custom_asset_ids[str(name)] = asset_id
        self._paks_for_asset_id[asset_id] = files_set
        self.replace_asset(name, new_data)
        for pak_name in in_paks:
            self.ensure_present(pak_name, asset_id)

        return asset_id

    def duplicate_asset(self, asset_id: AssetId, new_name: str) -> AssetId:
        """
        Creates a new asset named `new_name` with the contents of `asset_id`
        :return: Asset id of the new asset.
        """
        return self.add_new_asset(new_name, self.get_raw_asset(asset_id), ())

    def replace_asset(self, asset_id: NameOrAssetId, new_data: Resource):
        """
        Replaces an existing asset.
        See `add_new_asset` for new assets.
        """
        original_name = str(asset_id)
        asset_id = self._resolve_asset_id(asset_id)

        # Test if the asset exists
        if not self.does_asset_exists(asset_id):
            raise UnknownAssetId(asset_id, original_name)

        if isinstance(new_data, BaseResource):
            logger.debug("Encoding %s (%s, %s)", asset_id, original_name, new_data.resource_type())
            raw_asset = RawResource(
                type=new_data.resource_type(),
                data=new_data.build(),
            )

        else:
            raw_asset = new_data

        self._modified_resources[asset_id] = raw_asset

        return asset_id

    def add_or_replace_custom_asset(self, name: str, new_data: Resource) -> AssetId:
        """Adds a new asset named `name`, or replaces an existing one if it already exists."""
        if self.does_asset_exists(name):
            return self.replace_asset(name, new_data)
        else:
            return self.add_new_asset(name, new_data)

    def delete_asset(self, asset_id: NameOrAssetId):
        original_name = asset_id
        asset_id = self._resolve_asset_id(asset_id)

        # Test if the asset exists
        if not self.does_asset_exists(asset_id):
            raise UnknownAssetId(asset_id, original_name)

        self._modified_resources[asset_id] = None

        # If this asset id was previously ensured, remove that
        for ensured_ids in self._ensured_asset_ids.values():
            if asset_id in ensured_ids:
                ensured_ids.remove(asset_id)

    def ensure_present(self, pak_name: str, asset_id: NameOrAssetId):
        """
        Ensures the given pak has the given assets, collecting from other paks if needed.
        """
        if pak_name not in self._ensured_asset_ids:
            raise ValueError(f"Unknown pak_name: {pak_name}")

        original_name = asset_id
        asset_id = self._resolve_asset_id(asset_id)

        # Test if the asset exists
        if not self.does_asset_exists(asset_id):
            raise UnknownAssetId(asset_id, original_name)

        # If the pak already has the given asset, do nothing
        if pak_name not in self._paks_for_asset_id[asset_id]:
            self._ensured_asset_ids[pak_name].add(asset_id)

    def get_pak(self, pak_name: str) -> Pak:
        if pak_name not in self._ensured_asset_ids:
            raise ValueError(f"Unknown pak_name: {pak_name}. Known names: {tuple(self._ensured_asset_ids.keys())}")

        if pak_name not in self._in_memory_paks:
            logger.info("Reading %s", pak_name)
            with self.provider.open_binary(pak_name) as data:
                self._in_memory_paks[pak_name] = Pak.parse_stream(data, target_game=self.target_game)

        return self._in_memory_paks[pak_name]

    def _get_dependencies_for_asset(
        self,
        asset_id: NameOrAssetId,
        must_exist: bool,
    ) -> Iterator[Dependency]:
        if not self.target_game.is_valid_asset_id(asset_id):
            return

        if not self.does_asset_exists(asset_id):
            if must_exist:
                raise UnknownAssetId(asset_id)
            return

        asset_type = self.get_asset_type(asset_id)

        dep_cache = self._cached_dependencies
        deps: tuple[Dependency, ...] = ()

        if asset_id in dep_cache:
            # logger.debug(f"Fetching cached asset {asset_id:#8x}...")
            deps = dep_cache[asset_id]
        else:
            if dependency_cheating.should_cheat_asset(asset_type):
                deps = tuple(dependency_cheating.get_cheated_dependencies(self.get_raw_asset(asset_id), self))

            elif formats.has_resource_type(asset_type):
                if self.get_asset_format(asset_id).has_dependencies(self.target_game):
                    deps = tuple(self.get_parsed_asset(asset_id).dependencies_for())
                deps += tuple(self.target_game.special_ancs_dependencies(asset_id))

            else:
                logger.warning(f"Potential missing assets for {asset_type} {asset_id}")

            # logger.debug(f"Adding {asset_id:#8x} deps to cache...")
            dep_cache[asset_id] = deps

        yield from deps
        yield Dependency(asset_type, asset_id, False)

    def get_dependencies_for_asset(self, asset_id: NameOrAssetId, *, must_exist: bool = False) -> Iterator[Dependency]:
        override = asset_id in self.target_game.mlvl_dependencies_to_ignore
        try:
            deps = self._get_dependencies_for_asset(asset_id, must_exist)
        except DependenciesHandledElsewhere:
            return
        for it in deps:
            yield Dependency(it.type, it.id, it.exclude_for_mlvl or override)

    def get_dependencies_for_ancs(self, asset_id: NameOrAssetId, char_index: int | None = None):
        if not self.target_game.is_valid_asset_id(asset_id):
            return

        if (asset_type := self.get_asset_type(asset_id)) != "ANCS":
            raise ValueError(f"{hex(asset_id)} ({asset_type}) is not an ANCS!")

        if char_index is None:
            yield from self.get_dependencies_for_asset(asset_id)
            return

        if char_index in self._cached_ancs_per_char_dependencies[asset_id]:
            # logger.debug(f"Fetching cached asset {asset_id:#8x}...")
            deps = self._cached_ancs_per_char_dependencies[asset_id][char_index]
        else:
            deps = list(self.target_game.special_ancs_dependencies(asset_id))
            ancs: Ancs = self.get_parsed_asset(asset_id)
            deps.extend(ancs.ancs_dependencies_for(char_index=char_index))
            deps = tuple(deps)
            self._cached_ancs_per_char_dependencies[asset_id][char_index] = deps

        yield from deps
        yield Dependency("ANCS", asset_id)

    def _build_audio_group_dependency_table(self):
        atbl: Atbl | None = None
        agsc_ids: list[AssetId] = []

        for asset_id in self.all_asset_ids():
            asset_type = self.get_asset_type(asset_id)
            if asset_type == "ATBL":
                if atbl is not None:
                    logger.warning("Two ATBL files found!")
                atbl = self.get_parsed_asset(asset_id)
            elif asset_type == "AGSC":
                agsc_ids.append(asset_id)

        define_id_to_agsc: dict[int, AssetId] = {0xFFFF: None, -1: None}
        for agsc_id in agsc_ids:
            try:
                agsc = self.get_parsed_asset(agsc_id, type_hint=Agsc)
                for define_id in agsc.define_ids:
                    define_id_to_agsc[define_id] = agsc_id
            except Exception as e:
                raise Exception(f"Error parsing AGSC {hex(agsc_id)}: {e}")

        self._sound_id_to_agsc = {-1: None}
        for sound_id, define_id in enumerate(atbl.raw):
            if define_id in define_id_to_agsc:
                self._sound_id_to_agsc[sound_id] = define_id_to_agsc[define_id]

    def get_audio_group_dependency(self, sound_id: int) -> Iterator[Dependency]:
        if self._sound_id_to_agsc is None:
            self._build_audio_group_dependency_table()

        agsc = self._sound_id_to_agsc[sound_id]
        if agsc is None:
            return

        if self._audio_group_dependency is None:
            self._audio_group_dependency = tuple(
                self.get_asset(asset, Dgrp) for asset in self.target_game.audio_group_dependencies()
            )

        dep = Dependency("AGSC", agsc, False)
        if any((dep in deps.direct_dependencies) for deps in self._audio_group_dependency):
            return
        else:
            yield dep

    def _write_custom_names(self, output_path: Path):
        custom_names = output_path.joinpath("custom_names.json")
        with custom_names.open("w") as f:
            json.dump(
                dict(self._custom_asset_ids.items()),
                f,
                indent=4,
            )

    def save_modifications(self, output_path: Path):
        modified_paks = set()
        asset_ids_to_copy = {}

        for asset_id in self._modified_resources.keys():
            modified_paks.update(self._paks_for_asset_id[asset_id])

        # Make sure all paks were loaded
        for pak_name in modified_paks:
            self.get_pak(pak_name)

        # Read all asset ids we need to copy somewhere else
        for asset_ids in self._ensured_asset_ids.values():
            for asset_id in asset_ids:
                if asset_id not in asset_ids_to_copy:
                    asset_ids_to_copy[asset_id] = self.get_raw_asset(asset_id)

        # Update the PAKs
        for pak_name in modified_paks:
            logger.info("Updating %s", pak_name)
            pak = self._in_memory_paks.pop(pak_name)

            for asset_id, raw_asset in self._modified_resources.items():
                if pak_name in self._paks_for_asset_id[asset_id]:
                    if raw_asset is None:
                        pak.remove_asset(asset_id)
                    else:
                        pak.replace_asset(asset_id, raw_asset)

            # Add the files that were ensured to be present in this pak
            for asset_id in self._ensured_asset_ids[pak_name]:
                pak.add_asset(asset_id, asset_ids_to_copy[asset_id])

            # Write the data
            out_pak_path = output_path.joinpath(pak_name)
            logger.info("Writing %s", out_pak_path)
            out_pak_path.parent.mkdir(parents=True, exist_ok=True)
            with out_pak_path.open("w+b") as f:
                pak.build_stream(f)

        self._write_custom_names(output_path)
        self._modified_resources = {}
        self._update_headers()
