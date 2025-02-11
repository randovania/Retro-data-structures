from __future__ import annotations

from typing import TYPE_CHECKING

from tests import test_lib

from retro_data_structures.formats import RuleSet

if TYPE_CHECKING:
    from retro_data_structures.asset_manager import AssetManager
    from retro_data_structures.base_resource import AssetId


def test_compare_p2(prime2_asset_manager: AssetManager, rule_asset_id: AssetId) -> None:
    resource = prime2_asset_manager.get_raw_asset(rule_asset_id)
    decoded = RuleSet.parse(resource.data, target_game=prime2_asset_manager.target_game)
    encoded = decoded.build()

    decoded2 = RuleSet.parse(encoded, target_game=prime2_asset_manager.target_game)

    assert test_lib.purge_hidden(decoded2.raw) == test_lib.purge_hidden(decoded.raw)
