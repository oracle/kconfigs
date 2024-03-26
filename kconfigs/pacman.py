# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import posixpath
from pathlib import Path
from typing import Any

import aiofiles
from aiofiles.tempfile import TemporaryDirectory

from kconfigs.extractor import Extractor
from kconfigs.fetcher import Checksum
from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
from kconfigs.util import check_call
from kconfigs.util import download_file
from kconfigs.util import head_file


async def parse_desc(desc: Path) -> dict[str, str]:
    async with aiofiles.open(desc, "rt") as f:
        data = await f.read()
    key_val = {}
    for blob in data.split("\n\n"):
        if not blob:
            continue
        key, val = blob.split("\n", 1)
        key = key.strip("%")
        val = val.strip()
        key_val[key] = val
    return key_val


class PacmanFetcher(Fetcher):
    def __init__(
        self, saved_state: dict[str, Any], dc: DistroConfig, savedir: Path
    ):
        self.__last_modified: None | str = saved_state.get("last_modified")
        self.__latest_modified: None | str = None
        self.__latest_url: None | str = None
        self.index = dc.index
        self.arch = dc.arch
        assert dc.repo is not None
        self.repo = dc.repo
        self.dburl = posixpath.join(self.index, f"{dc.repo}.db.tar.gz")
        self.package = dc.package

    @classmethod
    def uid(cls, dc: DistroConfig) -> str:
        return dc.index

    def save_data(self) -> dict[str, Any]:
        return {"last_modified": self.__latest_modified or self.__last_modified}

    async def is_updated(self) -> bool:
        if not self.__latest_modified:
            headers = await head_file(self.dburl)
            self.__latest_modified = headers["Last-Modified"]
        return self.__latest_modified != self.__last_modified

    async def latest_version_url(self, _: str) -> tuple[str, Checksum | None]:
        assert self.__latest_modified
        async with TemporaryDirectory() as td:
            tdpath = Path(td)

            dbpath = tdpath / posixpath.split(self.dburl)[-1]
            await download_file(self.dburl, dbpath, always_download=True)
            await check_call(["tar", "xf", dbpath], cwd=tdpath)
            for dir in tdpath.glob(f"{self.package}-*"):
                desc = await parse_desc(dir / "desc")
                if desc["NAME"] == self.package:
                    break
            else:
                raise Exception(f"could not find package: {self.package}")
            checksum = ("sha256", desc["SHA256SUM"])
            url = posixpath.join(self.index, desc["FILENAME"])
            self.__latest_url = url
            return (url, checksum)

    async def signature_url(self, _: str) -> str | None:
        assert self.__latest_url is not None
        return self.__latest_url + ".sig"


class PacmanExtractor(Extractor):
    async def extract_kconfig(
        self, package: Path, output: Path, dc: DistroConfig
    ) -> None:
        async with TemporaryDirectory() as td:
            tdpath = Path(td)
            await check_call(["tar", "xf", package], cwd=tdpath)
            extractor = Path(__file__).absolute().parent / "extract-ikconfig"
            kernel_image = next(tdpath.glob("usr/lib/modules/*/vmlinuz"))
            config = await check_call(
                [extractor, kernel_image], capture_output=True
            )
            async with aiofiles.open(output, "wb") as f:
                await f.write(config)
