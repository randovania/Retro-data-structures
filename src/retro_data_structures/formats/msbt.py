from __future__ import annotations

import io
import typing

import construct
from construct import Const, Struct

from retro_data_structures.base_resource import AssetType, BaseResource, Dependency
from retro_data_structures.common_types import FourCC
from retro_data_structures.formats.form_descriptor import FormDescriptor

if typing.TYPE_CHECKING:
    from retro_data_structures.game_check import Game

MSBTHeader = construct.Aligned(
    16,
    Struct(
        magic=construct.Const(b"MsgStdBn"),
        bom=construct.Const(0xFEFF, construct.Int16ul),
        unk1=construct.Int16ul,
        maybe_major_version=construct.Int8ul,
        maybe_minor_version=construct.Int8ul,
        section_count=construct.Int16ul,
        unk3=construct.Int16ul,
        file_size=construct.Int32ul,
    ),
)


def TableHeader(magic: str):
    return construct.Aligned(
        16,
        Struct(
            magic=construct.Const(magic, FourCC),
            table_size=construct.Int32ul,
        ),
    )


class SectionBody(construct.Construct):
    def __init__(self, header_magic: str, entry: construct.Construct, has_entry_size: bool):
        super().__init__()
        self.header_magic = header_magic
        self.entry_header = construct.Int32ul
        self.entry = entry
        self.has_entry_size = has_entry_size
        self.int_type = typing.cast("construct.Construct", construct.Int32ul)

    def get_offset_from_header(self, item):
        return item

    def _header_for_entry(self, offset: int, entry):
        return offset

    def _context_for_entry(self, context, entry_header):
        return context

    def _parse(self, stream, context, path):
        table_size = TableHeader(self.header_magic)._parsereport(stream, context, path).table_size
        table_start = construct.stream_tell(stream, path)
        count = self.int_type._parsereport(stream, context, path)

        if self.has_entry_size:
            # Read the entry size. It's always 4.
            construct.Const(4, self.int_type)._parsereport(stream, context, path)

        entry_headers = construct.Array(count, self.entry_header)._parsereport(stream, context, path)

        all_offsets = [self.get_offset_from_header(entry_header) for entry_header in entry_headers]
        all_offsets.append(table_size)

        result = construct.ListContainer()
        for entry_header, next_offset in zip(entry_headers, all_offsets[1:]):
            this_offset = self.get_offset_from_header(entry_header)
            current_offset = construct.stream_tell(stream, path)
            if current_offset != table_start + this_offset:
                raise construct.CheckError("incorrect offset in data", path=path)

            new_context = self._context_for_entry(context, entry_header)
            entry_data = io.BytesIO(construct.stream_read(stream, next_offset - this_offset, path))
            result.append(self.entry._parsereport(entry_data, new_context, path))
            if not construct.stream_iseof(entry_data):
                raise construct.CheckError("expected entry to read entire data", path=path)

        return result

    def _build(self, obj, stream, context, path):
        items = []
        entry_headers = []

        count = len(obj)
        offset = self.int_type._sizeof(construct.Container(), path)
        if self.has_entry_size:
            offset += self.int_type._sizeof(construct.Container(), path)
        offset += count * self.entry_header._sizeof(context, path)

        for item in obj:
            new_context = self._context_for_entry(context, item)

            stream2 = io.BytesIO()
            self.entry._build(item, stream2, new_context, path)
            items.append(stream2.getvalue())
            entry_headers.append(
                self._header_for_entry(
                    offset,
                    item,
                )
            )
            offset += len(items[-1])

        # Build!
        TableHeader(self.header_magic)._build(
            construct.Container(
                table_size=offset,
            ),
            stream,
            context,
            path,
        )
        table_start = construct.stream_tell(stream, path)
        self.int_type._build(count, stream, context, path)

        if self.has_entry_size:
            # Read the entry size. It's always 4.
            construct.Const(4, self.int_type)._build(None, stream, context, path)

        construct.Array(count, self.entry_header)._build(entry_headers, stream, context, path)

        for entry_header, entry in zip(entry_headers, items):
            this_offset = self.get_offset_from_header(entry_header)
            current_offset = construct.stream_tell(stream, path)
            if current_offset != table_start + this_offset:
                raise construct.CheckError("incorrect offset in data", path=path)

            construct.stream_write(stream, entry, len(entry), path)


class LabelsSectionBody(SectionBody):
    def __init__(self):
        super().__init__(
            "LBL1",
            entry=construct.Array(
                lambda ctx: ctx.entry_header.string_count,
                Struct(
                    str=construct.PascalString(construct.Int8ul, "ascii"),
                    string_table_index=construct.Int32ul,
                ),
            ),
            has_entry_size=False,
        )
        self.entry_header = Struct(
            string_count=construct.Int32ul,
            string_offset=construct.Int32ul,
        )

    def get_offset_from_header(self, item):
        return item.string_offset

    def _header_for_entry(self, offset: int, entry):
        return construct.Container(
            string_offset=offset,
            string_count=len(entry),
        )

    def _context_for_entry(self, context, entry_or_header):
        new_context = construct.Container(
            _=context,
            _params=context._params,
            _root=None,
            _parsing=context._parsing,
            _building=context._building,
            _sizing=context._sizing,
            _io=context._io,
            _index=context.get("_index", None),
        )
        new_context._root = new_context._.get("_root", context)

        if context._building:
            new_context.entry_header = self._header_for_entry(0, entry_or_header)
        else:
            new_context.entry_header = entry_or_header

        return new_context


LabelsSection = construct.Aligned(16, LabelsSectionBody(), b"\xab")

AttributesSection = construct.Aligned(
    16,
    SectionBody(
        header_magic="ATR1",
        entry=construct.CString("utf_16_le"),
        has_entry_size=True,
    ),
    b"\xab",
)

TextsSection = construct.Aligned(
    16,
    SectionBody(
        header_magic="TXT2",
        entry=construct.StringEncoded(construct.GreedyBytes, "utf_16_le"),
        has_entry_size=False,
    ),
    b"\xab",
)


def Language(language_code: str):
    return Struct(
        header=Struct(
            magic=Const(language_code, FourCC),
            file_size=construct.Int32ul,
            unk=construct.Int32ul[4],
        ),
        contents=Struct(
            header=MSBTHeader,
            labels=LabelsSection,
            attributes=AttributesSection,
            texts=TextsSection,
        ),
    )


MSBT = FormDescriptor(
    "MSBT",
    10,
    10,
    Struct(
        us_english=Language("USEN"),
        eu_english=Language("EUEN"),
        eu_french=Language("EUFR"),
        us_french=Language("USFR"),
        eu_spanish=Language("EUSP"),
        eu_german=Language("EUGE"),
        eu_italian=Language("EUIT"),
        eu_dutch=Language("EUDU"),
        jp_japanese=Language("JPJP"),
        ko_korean=Language("KOKO"),
        ch_traditionalchinese=Language("CHTC"),
        ch_simplifiedchinese=Language("CHSC"),
        us_spanish=Language("USSP"),
    ),
)


class Msbt(BaseResource):
    @classmethod
    def resource_type(cls) -> AssetType:
        return "MSBT"

    @classmethod
    def construct_class(cls, target_game: Game) -> construct.Construct:
        return MSBT

    def dependencies_for(self) -> typing.Iterator[Dependency]:
        yield from []
