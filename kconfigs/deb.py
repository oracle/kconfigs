# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import posixpath
import re
import shutil
from pathlib import Path
from typing import Any

import aiofiles
from aiofiles.tempfile import TemporaryDirectory

from kconfigs.extractor import Extractor
from kconfigs.fetcher import Checksum
from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
from kconfigs.util import download_file
from kconfigs.util import download_file_mem_verified
from kconfigs.util import maybe_decompress


RPM_TO_DEB_ARCH = {
    "x86_64": "amd64",
    "aarch64": "arm64",
}


class DebFetcher(Fetcher):
    def __init__(
        self, saved_data: dict[str, Any], dc: DistroConfig, savedir: Path
    ):
        self.index = dc.index
        self.savedir = savedir
        self.__last_hash: None | str = saved_data.get("last_hash")
        self.__latest_hash: None | str = None
        self.__packages_path: None | str = None
        self.__packages_local: None | Path = None
        self.__arch = RPM_TO_DEB_ARCH.get(dc.arch, dc.arch)
        self.__category = dc.category or "main"
        assert dc.codename is not None
        self.__codename = dc.codename
        assert dc.key
        self.key = dc.key

    @classmethod
    def uid(cls, dc: DistroConfig) -> str:
        arch = RPM_TO_DEB_ARCH.get(dc.arch, dc.arch)
        return "-".join([dc.index, str(dc.codename), arch, str(dc.category)])

    def save_data(self) -> dict[str, Any]:
        return {"last_hash": self.__latest_hash or self.__last_hash}

    async def __query_latest_hash(self) -> None:
        url = posixpath.join(self.index, "dists", self.__codename, "Release")
        data_bytes = await download_file_mem_verified(
            url, self.key, suffix=".gpg"
        )
        data = data_bytes.decode("utf-8")
        entry_re = re.compile(r"^\s*([0-9a-f]+)\s+\d+\s+(.*)$", re.M)
        ix = data.index("SHA256:\n")
        desired_entries = [
            f"{self.__category}/binary-{self.__arch}/Packages.xz",
            f"{self.__category}/binary-{self.__arch}/Packages.bz2",
            f"{self.__category}/binary-{self.__arch}/Packages.gz",
        ]
        file_to_hash = {
            m.group(2): m.group(1) for m in entry_re.finditer(data, ix)
        }
        for file in desired_entries:
            if file in file_to_hash:
                self.__latest_hash = file_to_hash[file]
                self.__packages_path = file
                break
        else:
            raise Exception("Could not find Packages file")

    async def is_updated(self) -> bool:
        if not self.__latest_hash:
            await self.__query_latest_hash()
        return self.__latest_hash != self.__last_hash

    async def __fetch_latest_packages(self) -> None:
        if not self.__latest_hash:
            await self.__query_latest_hash()
        assert self.__latest_hash
        assert self.__packages_path
        url = posixpath.join(
            self.index, "dists", self.__codename, self.__packages_path
        )
        name = posixpath.basename(url)
        file = self.savedir / name
        await download_file(
            url,
            file,
            always_download=await self.is_updated(),
            checksum=("sha256", self.__latest_hash),
        )
        self.__packages_local = await maybe_decompress(file)

    async def __get_relevant_keys(
        self, flavor: str
    ) -> dict[str, dict[str, str]]:
        assert self.__packages_local
        async with aiofiles.open(self.__packages_local, "rt") as f:
            keys: dict[str, dict[str, str]] = {}
            in_sec = None
            pkgline = re.compile(f"Package: (linux-.*{flavor})")
            for line in await f.readlines():
                if in_sec and line.strip():
                    k, v = line.split(":", 1)
                    keys[in_sec][k.strip()] = v.strip()
                elif in_sec:
                    in_sec = None
                elif m := pkgline.fullmatch(line.strip()):
                    keys[m.group(1)] = {}
                    in_sec = m.group(1)
        return keys

    async def latest_version_url(self, pkg: str) -> tuple[str, Checksum | None]:
        if not self.__packages_local:
            await self.__fetch_latest_packages()
        m = re.fullmatch(r"linux-(.*)", pkg)
        assert m
        flavor = m.group(1)
        keys = await self.__get_relevant_keys(flavor)

        # For Ubuntu at least, the packages are wildly messed up.
        # Assume for a moment we're looking at flavor=generic.
        # We have "linux-generic" depending on "linux-image-generic", which
        # depends on "linux-image-$UNAME-generic" which depends on
        # "linux-modules-$UNAME-generic". Whew. All *we* want is the config,
        # which seems to be contained in linux-modules-$UNAME-generic. The
        # quickest route to this is to find "linux-image-$FLAVOR", get the
        # specific package name dependency ("linux-image-$UNAME-$FLAVOR"),
        # and then replace that with linux-modules.
        deps = keys[f"linux-image-{flavor}"]["Depends"].split(", ")
        for dep in deps:
            if " " in dep:
                # Contains " (additional junk)", strip it out
                dep, _ = dep.split(maxsplit=1)
            if dep.startswith("linux-image"):
                pkg = dep.replace("linux-image", "linux-modules")
                if pkg not in keys:
                    pkg = dep
                break
        else:
            raise Exception("Could not find specific linux-modules package")
        url = posixpath.join(self.index, keys[pkg]["Filename"])
        checksum = ("sha256", keys[pkg]["SHA256"])
        return (url, checksum)


class DebExtractor(Extractor):
    async def extract_kconfig(
        self, package: Path, output: Path, _: DistroConfig
    ) -> None:
        async with TemporaryDirectory() as td:
            tdpath = Path(td)
            proc = await asyncio.create_subprocess_exec(
                "dpkg-deb",
                "-x",
                package,
                tdpath,
            )
            code = await proc.wait()
            assert code == 0
            candidates = list(tdpath.glob("boot/config*"))
            assert len(candidates) == 1
            shutil.copyfile(candidates[0], output)
