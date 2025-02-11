from __future__ import annotations

from typing import TYPE_CHECKING

from retro_data_structures.formats.ancs import ANCS, Ancs
from retro_data_structures.formats.anim import ANIM, Anim
from retro_data_structures.formats.audio_group import AGSC, ATBL, Agsc, Atbl
from retro_data_structures.formats.char import CHAR, Char
from retro_data_structures.formats.cinf import CINF, Cinf
from retro_data_structures.formats.cmdl import CMDL, Cmdl
from retro_data_structures.formats.cskr import CSKR, Cskr
from retro_data_structures.formats.cspp import CSPP, Cspp
from retro_data_structures.formats.dgrp import DGRP, Dgrp
from retro_data_structures.formats.effect_script import (
    CRSC,
    DPSC,
    ELSC,
    PART,
    SPSC,
    SRSC,
    SWHC,
    WPSC,
    Crsc,
    Dpsc,
    Elsc,
    Part,
    Spsc,
    Srsc,
    Swhc,
    Wpsc,
)
from retro_data_structures.formats.evnt import EVNT, Evnt
from retro_data_structures.formats.mapa import MAPA, Mapa
from retro_data_structures.formats.mapu import MAPU, Mapu
from retro_data_structures.formats.mapw import MAPW, Mapw
from retro_data_structures.formats.mlvl import MLVL, Mlvl
from retro_data_structures.formats.mrea import MREA, Mrea
from retro_data_structures.formats.msbt import Msbt
from retro_data_structures.formats.pak import Pak
from retro_data_structures.formats.room import Room
from retro_data_structures.formats.rule import RULE, RuleSet
from retro_data_structures.formats.sand import SAND, Sand
from retro_data_structures.formats.savw import SAVW, Savw
from retro_data_structures.formats.scan import SCAN, Scan
from retro_data_structures.formats.strg import STRG, Strg
from retro_data_structures.formats.txtr import TXTR, Txtr

if TYPE_CHECKING:
    from construct import Construct

    from retro_data_structures.base_resource import AssetType, BaseResource

ALL_FORMATS = {
    "ANCS": ANCS,
    "ANIM": ANIM,
    "CHAR": CHAR,
    "CINF": CINF,
    "CMDL": CMDL,
    "CSKR": CSKR,
    "CSPP": CSPP,
    "DGRP": DGRP,
    "EVNT": EVNT,
    "MAPA": MAPA,
    "MAPW": MAPW,
    "MAPU": MAPU,
    "MLVL": MLVL,
    "MREA": MREA,
    "PART": PART,
    "SAND": SAND,
    "SAVW": SAVW,
    "SCAN": SCAN,
    "STRG": STRG,
    "TXTR": TXTR,
    "DPSC": DPSC,
    "WPSC": WPSC,
    "CRSC": CRSC,
    "SRSC": SRSC,
    "SPSC": SPSC,
    "ELSC": ELSC,
    "SWHC": SWHC,
    "AGSC": AGSC,
    "ATBL": ATBL,
    "RULE": RULE,
}

ALL_RESOURCE_TYPES = {
    "ANCS": Ancs,
    "ANIM": Anim,
    "CHAR": Char,
    "CINF": Cinf,
    "CMDL": Cmdl,
    "CSKR": Cskr,
    "CSPP": Cspp,
    "DGRP": Dgrp,
    "EVNT": Evnt,
    "MAPA": Mapa,
    "MAPU": Mapu,
    "MAPW": Mapw,
    "MLVL": Mlvl,
    "MREA": Mrea,
    "MSBT": Msbt,
    "PAK": Pak,
    "PART": Part,
    "ROOM": Room,
    "SAND": Sand,
    "SAVW": Savw,
    "SCAN": Scan,
    "STRG": Strg,
    "TXTR": Txtr,
    "DPSC": Dpsc,
    "WPSC": Wpsc,
    "CRSC": Crsc,
    "SRSC": Srsc,
    "SPSC": Spsc,
    "ELSC": Elsc,
    "SWHC": Swhc,
    "AGSC": Agsc,
    "ATBL": Atbl,
    "RULE": RuleSet,
}


def format_for(type_name: AssetType) -> Construct:
    return ALL_FORMATS[type_name.upper()]


def resource_type_for(type_name: AssetType) -> type[BaseResource]:
    return ALL_RESOURCE_TYPES[type_name.upper()]


def has_format(type_name: AssetType) -> bool:
    return type_name in ALL_FORMATS


def has_resource_type(type_name: AssetType) -> bool:
    return type_name in ALL_RESOURCE_TYPES


__all__ = ["format_for", "resource_type_for", "has_format", "has_resource_type"]
