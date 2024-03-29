# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import abc
import importlib
from functools import cache
from pathlib import Path

from kconfigs.fetcher import DistroConfig
from kconfigs.util import gpg_verify


class Extractor(abc.ABC):
    name: str

    async def verify_signature(
        self, package: Path, sig: Path, dc: DistroConfig
    ) -> None:
        """Override this if you have custom verification logic"""
        assert dc.key
        if await gpg_verify(package, sig, dc.key):
            print(f"Good GPG signature [{dc.key}]: {package.name}")
        else:
            raise Exception(f"Bad GPG signature [{dc.key}]: {package.name}")

    @abc.abstractmethod
    async def extract_kconfig(
        self, package: Path, output: Path, dc: DistroConfig
    ) -> None:
        """Extract the kconfig from the package into the output file"""

    @classmethod
    @cache
    def get(cls, kind: str) -> "Extractor":
        modname, klassname = kind.rsplit(".", maxsplit=1)
        mod = importlib.import_module(modname)
        klass = getattr(mod, klassname)
        klass.name = kind
        return klass()  # type: ignore
