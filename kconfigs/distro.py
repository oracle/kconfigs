# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
"""Configuration for one kernel source target."""

from dataclasses import dataclass


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
