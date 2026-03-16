from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.assets_data import AssetDatasetFetchError, _load_parser_config as load_asset_parser_config
from services.nhis_rates import NhisRatesFetchError, _load_parser_config as load_nhis_parser_config


class ParserConfigGateTest(unittest.TestCase):
    def test_nhis_parser_config_missing_raises_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.json"
            with patch("services.nhis_rates._PARSER_CONFIG_PATH", missing):
                with self.assertRaises(NhisRatesFetchError):
                    load_nhis_parser_config(strict=True)

    def test_asset_parser_config_missing_raises_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.json"
            with patch("services.assets_data._PARSER_CONFIG_PATH", missing):
                with self.assertRaises(AssetDatasetFetchError):
                    load_asset_parser_config(strict=True)


if __name__ == "__main__":
    unittest.main()
