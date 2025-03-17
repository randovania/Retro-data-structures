from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
import logging
import pprint
import typing
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from retro_data_structures import dependencies, formats
from retro_data_structures.asset_manager import AssetManager, FileProvider, IsoFileProvider, PathFileProvider
from retro_data_structures.construct_extensions.json import convert_to_raw_python
from retro_data_structures.conversion import conversions
from retro_data_structures.conversion.asset_converter import AssetConverter
from retro_data_structures.exceptions import UnknownAssetId
from retro_data_structures.formats import Mlvl, mlvl
from retro_data_structures.game_check import Game

if typing.TYPE_CHECKING:
    from retro_data_structures.base_resource import AssetId
    from retro_data_structures.formats.mrea import Area

types_per_game = {
    "metroid_prime_1": {
        "mlvl": mlvl.Prime1MLVL,
    },
    "metroid_prime_2": {
        "mlvl": mlvl.Prime2MLVL,
    },
    "metroid_prime_3": {
        "mlvl": mlvl.Prime3MLVL,
    },
}


def game_argument_type(s: str) -> Game:
    try:
        return Game(int(s))
    except ValueError:
        # not a number, look by name
        for g in Game:
            g = typing.cast("Game", g)
            if g.name.lower() == s.lower():
                return g
        raise ValueError(f"No enum named {s} found")


def add_game_argument(parser: argparse.ArgumentParser, name="--game"):
    choices = []
    for g in Game:
        g = typing.cast("Game", g)
        choices.append(g.value)
        choices.append(g.name)

    parser.add_argument(name, help="The game of the file", type=game_argument_type, choices=list(Game), required=True)


def add_provider_argument(parser: argparse.ArgumentParser):
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input-path", type=Path, help="Path to where to find pak files")
    group.add_argument("--input-iso", type=Path, help="Path to where to find ISO")


def get_provider_from_argument(args: argparse.Namespace) -> FileProvider:
    if args.input_path is not None:
        return PathFileProvider(args.input_path)
    else:
        return IsoFileProvider(args.input_iso)


def asset_id_conversion(x: str) -> AssetId:
    try:
        return uuid.UUID(x)
    except ValueError:
        return int(x, 0)


def _add_areas_command(area_tool: argparse.ArgumentParser) -> None:
    add_game_argument(area_tool)
    add_provider_argument(area_tool)
    area_sub = area_tool.add_subparsers(dest="area_command", required=True)
    area_sub.add_parser("list-areas", help="List all areas").add_argument("--world", help="Only in the given world")

    list_objs_cmd = area_sub.add_parser("list-objects", help="List all objects in an area")
    list_docks_cmd = area_sub.add_parser("list-docks", help="List all docks in an area")
    print_cmd = area_sub.add_parser("print-object", help="Print details about an object")
    for c in [list_objs_cmd, list_docks_cmd, print_cmd]:
        c.add_argument("world_id", type=lambda x: int(x, 0), help="Asset id of the world")
        c.add_argument("area_id", type=lambda x: int(x, 0), help="Asset id of the area")
    list_objs_cmd.add_argument("--layer", help="Only in the given layer")
    print_cmd.add_argument("instance_id", type=lambda x: int(x, 0), help="Instance id of the object to print")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()

    subparser = parser.add_subparsers(dest="command", required=True)

    ksy_export = subparser.add_parser("ksy-export")
    ksy_export.add_argument("output_path", type=Path)

    decode = subparser.add_parser("decode")
    add_game_argument(decode)
    decode.add_argument("--format", help="Hint the format of the file. Defaults to extension.")
    decode.add_argument("--re-encode", help="Re-encode afterwards and compares to the original.", action="store_true")
    decode.add_argument("input_path", type=Path, help="Path to the file")

    compare = subparser.add_parser("compare-files")
    add_game_argument(compare)
    compare.add_argument("--format", help="Hint the format of the file", required=True)
    compare.add_argument("--limit", help="Limit the number of files to test", type=int)
    compare.add_argument("input_path", type=Path, help="Path to the directory to glob")

    decode_from_paks = subparser.add_parser("decode-from-pak")
    add_game_argument(decode_from_paks)
    add_provider_argument(decode_from_paks)
    decode_from_paks.add_argument("asset_id", type=asset_id_conversion, help="Asset id to print")

    find_in_paks = subparser.add_parser("find-in-paks")
    add_game_argument(find_in_paks)
    add_provider_argument(find_in_paks)
    find_in_paks.add_argument("asset_id", type=asset_id_conversion, help="Asset id to find")

    extract = subparser.add_parser("extract")
    add_game_argument(extract)
    add_provider_argument(extract)
    extract.add_argument("asset_id", type=asset_id_conversion, help="Asset id to extract")
    extract.add_argument("destination", type=Path, help="Directory to write to")

    deps = subparser.add_parser("list-dependencies")
    add_game_argument(deps)
    add_provider_argument(deps)
    g = deps.add_mutually_exclusive_group()
    g.add_argument("--asset-ids", type=lambda x: int(x, 0), nargs="+", help="Asset id to list dependencies for")
    g.add_argument("--asset-type", type=str, help="List dependencies for all assets of the given type.")

    convert = subparser.add_parser("convert")
    add_game_argument(convert, "--source-game")
    add_game_argument(convert, "--target-game")
    add_provider_argument(convert)
    convert.add_argument("asset_ids", type=asset_id_conversion, nargs="+", help="Asset id to list dependencies for")

    area_tool = subparser.add_parser("areas")
    _add_areas_command(area_tool)

    hash_tool = subparser.add_parser("hash")
    add_game_argument(hash_tool)
    add_provider_argument(hash_tool)

    return parser


def do_ksy_export(args: argparse.Namespace) -> None:
    output_path: Path = args.output_path
    output_path.mkdir(parents=True, exist_ok=True)

    for game, game_formats in types_per_game.items():
        for format_name, cls in game_formats.items():
            print(f"Exporting {game} / {format_name}")
            cls.export_ksy(f"{game}_{format_name}", output_path.joinpath(f"{game}_{format_name}.ksy"))


def dump_to(path: Path, decoded: object) -> None:
    def default(o):
        if callable(o):
            o = o()
        if isinstance(o, bytes):
            return len(o)

        raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")

    with path.open("w") as f:
        x = convert_to_raw_python(decoded)
        f.write(json.JSONEncoder(indent=4, default=default).encode(x))


def do_decode(args):
    input_path: Path = args.input_path
    file_format = args.format
    game: Game = args.game
    re_encode = args.re_encode

    if file_format is None:
        file_format = input_path.suffix[1:]

    construct_class = formats.format_for(file_format)

    raw = input_path.read_bytes()
    decoded_from_raw = construct_class.parse(raw, target_game=game)
    print(decoded_from_raw)

    if re_encode:
        encoded = construct_class.build(decoded_from_raw, target_game=game)
        if raw != encoded:
            print(f"{input_path}: Results differ (len(raw): {len(raw)}; len(encoded): {len(encoded)})")


def do_decode_from_pak(args):
    game: Game = args.game
    asset_id: int = args.asset_id

    asset_manager = AssetManager(get_provider_from_argument(args), game)
    print(asset_manager.get_parsed_asset(asset_id).raw)


def do_find_in_paks(args):
    game: Game = args.game
    asset_id: int = args.asset_id

    asset_manager = AssetManager(get_provider_from_argument(args), game)
    print(list(asset_manager.find_paks(asset_id)))


def do_extract(args):
    game: Game = args.game
    asset_id: int = args.asset_id
    destination: Path = args.destination

    asset_manager = AssetManager(get_provider_from_argument(args), game)
    target_asset = asset_manager.get_raw_asset(asset_id)

    destination.mkdir(parents=True, exist_ok=True)
    destination.joinpath(f"{asset_id}.{target_asset.type.lower()}").write_bytes(target_asset.data)


def list_dependencies(args):
    game: Game = args.game
    asset_ids: list[int]

    asset_provider = AssetManager(get_provider_from_argument(args), game)
    if args.asset_ids is not None:
        asset_ids = args.asset_ids
    else:
        asset_ids = [
            asset_id
            for asset_id in asset_provider.all_asset_ids()
            if asset_provider.get_asset_type(asset_id) == args.asset_type.upper()
        ]

    for asset_type, asset_id in dependencies.recursive_dependencies_for(asset_provider, asset_ids):
        print(f"{asset_type}: {hex(asset_id)}")


def do_convert(args):
    source_game: Game = args.source_game
    target_game: Game = args.target_game
    asset_ids: list[int] = args.asset_ids

    next_generated_id = 0xFFFF0000
    asset_manager = AssetManager(get_provider_from_argument(args), source_game)

    def id_generator(asset_type):
        # Proper implementation would need an AssetManager for the target game.
        nonlocal next_generated_id
        result = next_generated_id
        next_generated_id = result + 1
        return result

    converter = AssetConverter(
        target_game=target_game,
        asset_providers={source_game: asset_manager},
        id_generator=id_generator,
        converters=conversions.converter_for,
    )

    for asset_id in asset_ids:
        converted = converter.convert_asset_by_id(asset_id, source_game)

        print(
            "\n========================="
            f"\n* Original Id: {asset_id:08x}"
            f"\n* Target Id: {converted.id:08x}"
            f"\n* Asset Type: {converted.type}"
            f"\n\n{converted.resource}"
        )

        for dependency in dependencies.direct_dependencies_for(converted.resource, converted.type, target_game):
            print(f"* Dependency: {dependency[1]:08x} ({dependency[0]})")

    print("==================\n>> All converted assets")
    reverse_converted_ids: dict[AssetId, tuple[Game, AssetId]] = {v: k for k, v in converter.converted_ids.items()}

    for converted_asset in converter.converted_assets.values():
        print(
            f" {converted_asset.type}: {converted_asset.id:08x} from {reverse_converted_ids[converted_asset.id][1]:08x}"
            f" ({reverse_converted_ids[converted_asset.id][0].name})"
        )


def decode_encode_compare_file(file_path: Path, game: Game, file_format: str):
    construct_class = formats.format_for(file_format)

    try:
        raw = file_path.read_bytes()
        decoded_from_raw = construct_class.parse(raw, target_game=game)

        sections = []
        for group in decoded_from_raw.section_groups:
            sections.extend(group.sections)

        encoded = construct_class.build(decoded_from_raw, target_game=game)

        if raw != encoded and raw.rstrip(b"\xff") != encoded:
            return f"{file_path}: Results differ (len(raw): {len(raw)}; len(encoded): {len(encoded)})"
        return None

    except Exception as e:
        return f"{file_path}: Received error - {e}"


async def compare_all_files_in_path(args):
    input_path: Path = args.input_path
    file_format: str = args.format
    game: Game = args.game
    limit: int | None = args.limit

    def apply_limit(it):
        if limit is None:
            return it
        else:
            return itertools.islice(it, limit)

    loop = asyncio.get_running_loop()

    try:
        import tqdm
    except ImportError:
        tqdm = None

    errors = []

    with ProcessPoolExecutor(max_workers=1) as executor:
        files = apply_limit(input_path.rglob(f"*.{file_format.upper()}"))
        if tqdm is not None:
            files = tqdm.tqdm(files, unit=" file")

        results = [loop.run_in_executor(executor, decode_encode_compare_file, f, game, file_format) for f in files]
        as_completed = asyncio.as_completed(results)
        if tqdm is not None:
            as_completed = tqdm.tqdm(as_completed, total=len(results), unit=" file")

        for c in as_completed:
            message = await c
            if message:
                if tqdm is not None:
                    errors.append(message)
                    as_completed.set_postfix_str(f"{len(errors)} errors")
                else:
                    print(message)
            raise SystemExit

    if errors:
        print(f"{len(errors)} errors:")
        for m in errors:
            print(m)


def _list_areas_command(args, asset_manager: AssetManager):
    if args.world:
        ids = [args.world]
    else:
        ids = [i for i in asset_manager.all_asset_ids() if asset_manager.get_asset_type(i) == "MLVL"]

    for mlvl_id in ids:
        mlvl = asset_manager.get_file(mlvl_id, Mlvl)
        try:
            world_name = mlvl.world_name
        except UnknownAssetId:
            world_name = "~no name~"

        for area in mlvl.areas:
            print(f"{world_name} (0x{mlvl_id:08x}) - {area.name}: {area.mrea_asset_id}")


def _list_objects_command(args, area: Area):
    for layer in area.all_layers:
        if not args.layer or args.layer == layer.name:
            for obj in layer.instances:
                print(f"{layer.name} - {obj} ({obj.name})")


def _list_docks_command(args, area: Area):
    docks = area._raw.docks
    print(f"{len(docks)} docks found")
    for i, dock in enumerate(docks):
        print(f"Dock {i}. {len(dock.connecting_dock)} connections, {len(dock.dock_coordinates)} coordinates.")

        print("> Connections:")
        for k, connection in enumerate(dock.connecting_dock):
            print(f"{k:>2}: area {connection.area_index:>2}, dock index: {connection.dock_index}")

        print("> Coordinates:")
        for k, coordinates in enumerate(dock.dock_coordinates):
            print(f"{k}: {list(coordinates)}")


def _print_object_command(args, area: Area):
    obj = area.get_instance(args.instance_id)
    pprint.pp(obj.get_properties(), width=200)
    print(f"Connections from: ({len(obj.connections)} total)")
    for con in obj.connections:
        print(con)


def do_area_command(args):
    game: Game = args.game
    asset_manager = AssetManager(get_provider_from_argument(args), game)

    if args.area_command == "list-areas":
        _list_areas_command(args, asset_manager)
        return

    world = asset_manager.get_file(args.world_id, Mlvl)
    area = world.get_area(args.area_id)

    if args.area_command == "list-objects":
        _list_objects_command(args, area)

    elif args.area_command == "list-docks":
        _list_docks_command(args, area)

    elif args.area_command == "print-object":
        _print_object_command(args, area)

    else:
        raise ValueError(f"Unknown command: {args.area_command}")


def hash_data(data: bytes) -> str:
    return "sha256: " + hashlib.sha256(data).hexdigest()


def hash_mlvl(level_id: AssetId, level: Mlvl) -> None:
    try:
        world_name = level.world_name
    except UnknownAssetId:
        world_name = "~no name~"

    print(f"0x{level_id:08X} ({world_name}):")
    for area in level.areas:
        print(f"    0x{area.mrea_asset_id:08X}; {area.name}")

        raw_sections = area.mrea.get_raw_section("script_layers_section")
        layers = list(area.layers)

        if len(raw_sections) != len(layers):
            for i, raw_section in enumerate(raw_sections):
                print(f"    - layer {i:>2}; {hash_data(raw_section)}")

        for layer in area.layers:
            if len(raw_sections) == len(layers):
                print(f"    - layer {layer.index:>2} {hash_data(raw_sections[layer.index])} ({layer.name}):")
            else:
                print(f"    - layer {layer.index:>2} ({layer.name}):")

            for instance in layer.instances:
                print(f"        - instance {instance.id}; {hash_data(instance.raw_properties)}")


def do_hash_command(args: argparse.Namespace) -> None:
    game: Game = args.game
    asset_manager = AssetManager(get_provider_from_argument(args), game)
    provider = asset_manager.provider

    for file in provider.get_file_list():
        print(f"{file}:")
        if file.endswith(".pak"):
            pak = asset_manager.get_pak(file)
            for asset_id, asset in pak.get_all_assets():
                print(f"    {asset.type} 0x{asset_id:08X}; {hash_data(asset.data)}")
        else:
            with provider.open_binary(file) as f:
                content = f.read()
            print(f"    {hash_data(content)}")

    mlvl_ids = [i for i in asset_manager.all_asset_ids() if asset_manager.get_asset_type(i) == "MLVL"]

    print("Worlds:")
    for mlvl_id in mlvl_ids:
        hash_mlvl(mlvl_id, asset_manager.get_file(mlvl_id, Mlvl))


def handle_args(args) -> None:
    if args.command == "ksy-export":
        do_ksy_export(args)
    elif args.command == "decode":
        do_decode(args)
    elif args.command == "decode-from-pak":
        do_decode_from_pak(args)
    elif args.command == "find-in-paks":
        do_find_in_paks(args)
    elif args.command == "extract":
        do_extract(args)
    elif args.command == "list-dependencies":
        list_dependencies(args)
    elif args.command == "convert":
        do_convert(args)
    elif args.command == "compare-files":
        asyncio.run(compare_all_files_in_path(args))
    elif args.command == "areas":
        do_area_command(args)
    elif args.command == "hash":
        do_hash_command(args)
    else:
        raise ValueError(f"Unknown command: {args.command}")


def main():
    logging.basicConfig(level=logging.INFO)
    handle_args(create_parser().parse_args())
