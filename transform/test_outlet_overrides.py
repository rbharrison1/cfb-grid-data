#!/usr/bin/env python3
"""Smoke tests for bq_transform._apply_outlet_overrides and the
config/outlet_overrides.py entries it reads.

Added for 2026 week 12 Kentucky @ Missouri, which fbschedules kept listing on
ESPN when it's on SECN -- every daily transform reverted a manual Mongo fix.
"""

import pandas as pd

from bq_transform import _apply_outlet_overrides
from config.networks import NETWORK_COLUMNS
from config.outlet_overrides import OUTLET_OVERRIDES


def _df():
    return pd.DataFrame({
        'id': [1, 2],
        'outlet': ['ESPN', 'ABC'],
        'outletOptions': [['ESPN', 'ABC'], None],
    })


def test_override_wins_and_clears_options():
    df = _apply_outlet_overrides(_df(), {1: 'SECN'})
    assert df.loc[0, 'outlet'] == 'SECN'
    assert df.loc[0, 'outletOptions'] is None


def test_other_games_untouched():
    df = _apply_outlet_overrides(_df(), {1: 'SECN'})
    assert df.loc[1, 'outlet'] == 'ABC'


def test_no_matching_overrides_is_a_noop():
    df = _apply_outlet_overrides(_df(), {999: 'SECN'})
    assert list(df['outlet']) == ['ESPN', 'ABC']


def test_every_override_is_a_grid_column():
    # An outlet outside NETWORK_COLUMNS gets the game dropped from the grid.
    bad = {gid: outlet for gid, outlet in OUTLET_OVERRIDES.items() if outlet not in NETWORK_COLUMNS}
    assert not bad, f"overrides not in NETWORK_COLUMNS: {bad}"


if __name__ == "__main__":
    test_override_wins_and_clears_options()
    test_other_games_untouched()
    test_no_matching_overrides_is_a_noop()
    test_every_override_is_a_grid_column()
    print("All outlet-override tests passed.")
