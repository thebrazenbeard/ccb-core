"""Installed distribution identity must match the package's public version."""

import unittest
from importlib.metadata import PackageNotFoundError, version

import chat_bus


class InstalledVersionTests(unittest.TestCase):
    def test_package_version_matches_installed_ccb_core_distribution(self) -> None:
        try:
            installed_version = version("ccb-core")
        except PackageNotFoundError:
            self.skipTest("ccb-core distribution is not installed")
        self.assertEqual(chat_bus.__version__, installed_version)


if __name__ == "__main__":
    unittest.main()
