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
from typing import Mapping
from typing import TypedDict
from typing import cast

from aiofiles.tempfile import TemporaryDirectory

from kconfigs.distro import DistroConfig
from kconfigs.extractor import Extractor
from kconfigs.index import Index
from kconfigs.index import IndexId
from kconfigs.index import alru_cache
from kconfigs.model import JSON
from kconfigs.model import Artifact
from kconfigs.model import IndexState
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


class UpstreamKernelData(TypedDict):
    version: str
    url: str


class UpstreamIndexStateData(TypedDict):
    kernels: tuple[UpstreamKernelData, ...]


def upstream_data(state: IndexState) -> UpstreamIndexStateData:
    return cast(UpstreamIndexStateData, state.data)


def _parse_feed(data: bytes) -> list[UpstreamKernel]:
    tree = ET.fromstring(data.decode("utf-8"))
    return [
        UpstreamKernel.from_item(item)
        for item in tree.findall("./channel/item")
    ]


def _kernel_matches_release(kernel: UpstreamKernelData, release: str) -> bool:
    return (
        kernel["version"] == release
        or kernel["version"].startswith(release + ".")
        or kernel["version"].startswith(release + "-")
    )


def _signature_url(url: str, key: str | None) -> str | None:
    if not key:
        return None
    tarbase, _ = posixpath.splitext(url)
    return tarbase + ".sign"


class UpstreamIndex(Index):
    def __init__(self, index_id: IndexId, path: Path):
        super().__init__(index_id, path)
        if not isinstance(index_id, str):
            raise TypeError(
                f"{type(self).__name__} requires str index ID, "
                f"not {type(index_id).__name__}"
            )
        self.index = index_id

    @classmethod
    def index_id(cls, dc: DistroConfig) -> str:
        return dc.index

    @alru_cache
    async def check(self) -> IndexState:
        kernels = tuple(
            UpstreamKernelData(version=kernel.version, url=kernel.url)
            for kernel in _parse_feed(await download_file_mem(self.index))
        )
        data: UpstreamIndexStateData = {"kernels": kernels}
        return IndexState(
            self.name, str(self.id), cast(Mapping[str, JSON], data)
        )

    async def resolve(self, dc: DistroConfig) -> Artifact:
        if self.index_id(dc) != self.id:
            raise ValueError(
                f"{type(self).__name__}.resolve() got distro for "
                f"{self.index_id(dc)}, expected {self.id}"
            )
        assert dc.version is not None
        state = await self.check()
        for kernel in upstream_data(state)["kernels"]:
            if _kernel_matches_release(kernel, dc.version):
                return Artifact(
                    url=kernel["url"],
                    checksum=None,
                    signature_url=_signature_url(kernel["url"], dc.key),
                    source_index_state=state,
                    version=kernel["version"],
                )
        raise Exception(f"Could not find upstream kernel {dc.version}")


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
