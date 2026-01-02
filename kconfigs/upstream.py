# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import os
import posixpath
import re
import shutil
import xml.etree.ElementTree as ET
from asyncio.subprocess import DEVNULL
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiofiles.tempfile import TemporaryDirectory

from kconfigs.extractor import Extractor
from kconfigs.fetcher import Checksum
from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
from kconfigs.util import check_call
from kconfigs.util import download_file_mem
from kconfigs.util import gpg_verify
from kconfigs.util import maybe_decompress


UPSTREAM_ARCH = {
    "aarch64": "arm64",
}


@dataclass
class UpstreamKernel:
    version: str
    url: str

    @classmethod
    def from_item(cls, item: ET.Element) -> "UpstreamKernel":
        title_elem = item.find("title")
        description_elem = item.find("description")
        assert title_elem is not None
        assert title_elem.text is not None
        assert description_elem is not None
        assert description_elem.text is not None
        version = title_elem.text.split(":")[0]
        if "linux-next" in title_elem.text:
            # Dummy url for linux-next
            return cls(version, "linux-next")
        expr = re.compile(
            r"https?://.*/linux-[0-9a-zA-Z.-]+\.tar\.(?:gz|xz|bz2|zst)"
        )
        url = expr.findall(description_elem.text)[0]
        return cls(version, url)


class UpstreamFetcher(Fetcher):
    def __init__(
        self, saved_state: dict[str, Any], dc: DistroConfig, savedir: Path
    ):
        # This is the full version of the last release downloaded
        self.__last_version: None | str = saved_state.get("last_version")
        self.__latest_version: None | str = None
        self.__latest_url: None | str = None
        # This is the prefix of the stable release, e.g. 4.14 or 6.5
        assert dc.version is not None
        self.release = dc.version
        self.index = dc.index
        self.arch = dc.arch
        self.savedir = savedir
        self.key = dc.key

    @classmethod
    def uid(cls, dc: DistroConfig) -> str:
        return f"upstream-{dc.version}-{dc.arch}"

    def save_data(self) -> dict[str, Any]:
        return {"last_version": self.__latest_version or self.__last_version}

    async def is_updated(self) -> bool:
        if not self.__latest_version:
            data = await download_file_mem(self.index)
            tree = ET.fromstring(data.decode("utf-8"))
            for item in tree.findall("./channel/item"):
                kernel = UpstreamKernel.from_item(item)
                # Use 6.1.15 or 6.1-rc5 for release "6.1",
                # but do not use 6.10!
                if (
                    kernel.version == self.release
                    or kernel.version.startswith(self.release + ".")
                    or kernel.version.startswith(self.release + "-")
                ):
                    self.__latest_version = kernel.version
                    self.__latest_url = kernel.url
                    break
            else:
                raise Exception(
                    f"Could not find upstream kernel {self.release}"
                )
        return self.__latest_version != self.__last_version

    async def signature_url(self, _: str) -> str | None:
        assert self.__latest_url
        if self.key:
            tarbase, _ = posixpath.splitext(self.__latest_url)
            return tarbase + ".sign"
        else:
            return None

    async def latest_version_url(self, _: str) -> tuple[str, Checksum | None]:
        assert self.__latest_url
        return (self.__latest_url, None)


class DefconfigExtractor(Extractor):
    async def verify_signature(
        self, package: Path, sig: Path, dc: DistroConfig
    ) -> None:
        if dc.key == "NOVERIFY-GITHUB":
            return
        assert dc.key is not None
        decompressed_tar = await maybe_decompress(package)
        if await gpg_verify(decompressed_tar, sig, dc.key):
            print(f"Good GPG signature [{dc.key}]: {package.name}")
            # We don't need the tar file, and it takes up too much disk space
            decompressed_tar.unlink()
        else:
            raise Exception(f"Bad GPG signature [{dc.key}]: {package.name}")

    async def extract_kconfig(
        self,
        package: Path,
        output: Path,
        dc: DistroConfig,
    ) -> None:
        # The O= inherited in the environment from "make run" is also used in
        # the kernel makefiles. Strip it out here to avoid issues.
        if "O" in os.environ:
            del os.environ["O"]
        if "MAKEFLAGS" in os.environ:
            del os.environ["MAKEFLAGS"]
        async with TemporaryDirectory() as td:
            tdpath = Path(td)
            arch = UPSTREAM_ARCH.get(dc.arch, dc.arch)

            await check_call(
                ["tar", "xf", package],
                cwd=tdpath,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )
            subdirs = list(tdpath.iterdir())
            assert len(subdirs) == 1
            extract_dir = subdirs[0]
            await check_call(
                ["make", f"ARCH={arch}", "defconfig"],
                cwd=extract_dir,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )
            shutil.copyfile(extract_dir / ".config", output)
