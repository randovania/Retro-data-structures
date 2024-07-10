from __future__ import annotations

import collections
import dataclasses
import io
import typing

import construct

from retro_data_structures.disc import disc_common
from retro_data_structures.disc.gc_disc import GcDisc
from retro_data_structures.disc.wii_disc import WiiDisc, WiiPartition
from retro_data_structures.formats import dol

if typing.TYPE_CHECKING:
    from pathlib import Path


class UnsupportedDiscFormat(Exception):
    pass


@dataclasses.dataclass
class FileEntry:
    offset: int
    size: int


FileTree: typing.TypeAlias = dict[str, typing.Union[FileEntry, "FileTree"]]


def decode_into_file_tree(fst) -> FileTree:
    file_tree: dict = {}
    current_dir = file_tree

    end_folder = collections.defaultdict(list)

    names_stream = io.BytesIO(fst.names)
    for i, file in enumerate(fst.file_entries):
        if i == 0:
            continue

        if i in end_folder:
            current_dir = end_folder.pop(i)[0]

        names_stream.seek(file.file_name)
        name = construct.CString("ascii").parse_stream(names_stream)
        if file.is_directory:
            new_dir = {}
            end_folder[file.param].append(current_dir)
            current_dir[name] = new_dir
            current_dir = new_dir
        else:
            current_dir[name] = FileEntry(
                offset=file.offset,
                size=file.param,
            )

    return file_tree


CommonGCWiiHeader = construct.Struct(
    game_code=construct.Bytes(4),
    maker_code=construct.Bytes(2),
    disc_id=construct.Int8ub,  # for multi-disc games
    version=construct.Int8ub,
    audio_streaming=construct.Int8ub,
    stream_buffer_size=construct.Int8ub,
    _unused_a=construct.Const(b"\x00" * 14),
    wii_magic_word=construct.Int32ub,  # 0x5D1C9EA3
    gc_magic_word=construct.Int32ub,  # 0xC2339F3D
)


class GameDisc:
    _raw: construct.Container
    _file_path: Path
    _file_tree: FileTree

    def __init__(self, raw: construct.Container, file_path: Path, is_wii: bool):
        self._raw = raw
        self._file_path = file_path
        self._is_wii = is_wii
        self._file_tree = decode_into_file_tree(raw.data_partition.fst)

    @classmethod
    def parse(cls, file_path: Path) -> GameDisc:
        with file_path.open("rb") as source:
            header = CommonGCWiiHeader.parse_stream(source)
            source.seek(0)

            if header.wii_magic_word == 0x5D1C9EA3:
                data = WiiDisc.parse_stream(source)
                is_wii = True
            elif header.gc_magic_word == 0xC2339F3D:
                data = GcDisc.parse_stream(source)
                is_wii = False
            else:
                raise UnsupportedDiscFormat("Unknown disc format")

        return GameDisc(data, file_path, is_wii)

    def _get_file_entry(self, name: str) -> FileEntry:
        file_entry = self._file_tree
        for segment in name.split("/"):
            file_entry = file_entry[segment]

        if isinstance(file_entry, FileEntry):
            return file_entry
        else:
            raise OSError(f"{name} is a directory")

    def files(self) -> list[str]:
        result = []

        def recurse(parent: str, tree: FileTree) -> None:
            for key, item in tree.items():
                name = f"{parent}/{key}" if parent else key

                if isinstance(item, FileEntry):
                    result.append(name)
                else:
                    recurse(name, item)

        recurse("", self._file_tree)
        return result

    def _open_data_at_offset(self, offset: int, size: int) -> disc_common.DiscFileReader:
        if self._is_wii:
            assert isinstance(self._raw.data_partition, WiiPartition)
            return self._raw.data_partition.begin_read_stream(self._file_path, offset, size)
        else:
            return disc_common.DiscFileReader(self._file_path, offset, size)

    def open_binary(self, name: str) -> disc_common.DiscFileReader:
        entry = self._get_file_entry(name)
        return self._open_data_at_offset(entry.offset, entry.size)

    def read_binary(self, name: str) -> bytes:
        entry = self._get_file_entry(name)
        with self._open_data_at_offset(entry.offset, entry.size) as file:
            return file.read(entry.size)

    def get_dol(self) -> bytes:
        disc_header = self._raw.data_partition.disc_header
        with self._open_data_at_offset(disc_header.main_executable_offset, -1) as file:
            header = dol.DolHeader.parse_stream(file)
            dol_size = dol.calculate_size_from_header(header)

        with self._open_data_at_offset(disc_header.main_executable_offset, dol_size) as file:
            return file.read()
