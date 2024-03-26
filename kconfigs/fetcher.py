# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
"""
Interface for fetchers, which get the latest kernel releases
"""
import abc
import importlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from typing import Type


Checksum = tuple[str, str]


@dataclass
class DistroConfig:
    """Represents a linux distro version for a specific arch & kernel"""

    name: str
    arch: str
    package: str
    fetcher: str
    extractor: str
    index: str
    do_update: bool = True
    version: str | None = None
    key: str | None = None

    codename: str | None = None
    category: str | None = None
    repo: str | None = None

    @property
    def unique_name(self) -> str:
        if self.version:
            return f"{self.name} {self.version} {self.arch}"
        else:
            return f"{self.name} {self.arch}"


class Fetcher(abc.ABC):
    name: str
    index: str

    @abc.abstractmethod
    def __init__(
        self, saved_state: dict[str, Any], dc: DistroConfig, savedir: Path
    ):
        """
        Initialize the fetcher with saved state and the package index URL.
        """

    @classmethod
    def uid(cls, dc: DistroConfig) -> str:
        """Unique ID for this fetcher, to enable reuse between distro instances"""
        raise NotImplementedError()

    @abc.abstractmethod
    def save_data(self) -> dict[str, Any]:
        """
        Return save data to avoid updating metadata or packages next time.
        """

    @abc.abstractmethod
    async def is_updated(self) -> bool:
        """
        Check whether the index metadata is updated

        If the metadata is not updated, we can assume that none of the packages
        are updated, so we don't need to download package metadata or any
        packages!
        """

    @abc.abstractmethod
    async def latest_version_url(
        self, package: str
    ) -> tuple[str, Checksum | None]:
        """Determine the url of the latest version of package"""

    async def signature_url(self, _: str) -> str | None:
        """Return the url of the GPG signature for the latest version"""
        return None

    @classmethod
    @cache
    def get(cls, kind: str) -> Type["Fetcher"]:
        modname, klassname = kind.rsplit(".", maxsplit=1)
        mod = importlib.import_module(modname)
        klass = getattr(mod, klassname)
        klass.name = kind
        return klass  # type: ignore
