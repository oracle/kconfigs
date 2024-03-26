# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import re
from asyncio.subprocess import DEVNULL
from pathlib import Path
from typing import Any

import aiofiles
from aiofiles.tempfile import TemporaryDirectory

from kconfigs.extractor import Extractor
from kconfigs.fetcher import Checksum
from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
from kconfigs.util import check_call
from kconfigs.util import download_file_mem


class AndroidGkiFetcher(Fetcher):
    def __init__(
        self, saved_state: dict[str, Any], dc: DistroConfig, savedir: Path
    ):
        self.index = dc.index

    @classmethod
    def uid(cls, dc: DistroConfig) -> str:
        return dc.index

    def save_data(self) -> dict[str, Any]:
        return {}

    async def is_updated(self) -> bool:
        return True

    async def latest_version_url(self, _: str) -> tuple[str, Checksum | None]:
        data = await download_file_mem(self.index)
        page = data.decode("utf-8")
        expr = re.compile(
            r"https://.*gki-certified-boot-android\d+-\d+\.\d+-\d{4}-\d{2}_r\d+\.zip"
        )
        # The names are like: android12-5.10-2023-03_r3.zip
        # These almost naturally sort alphanumerically, but not quite. The
        # prefix (android12-5.10) is constant, and only the YYYY-MM_rX value
        # changes. However that X may be single or double digit, so we need to
        # parse it and sort numerically.
        links: list[str] = list(set(expr.findall(page)))
        verexpr = re.compile(r"^.*(\d{4})-(\d{2})_r(\d+)\.zip$")

        def key_fn(link: str) -> tuple[int, int, int]:
            m = verexpr.fullmatch(link)
            assert m
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

        links.sort(key=key_fn)
        return (links[-1], None)


class AndroidGkiExtractor(Extractor):
    async def extract_kconfig(
        self, package: Path, output: Path, dc: DistroConfig
    ) -> None:
        async with TemporaryDirectory() as td:
            tdpath = Path(td)

            await check_call(
                ["unzip", package],
                cwd=tdpath,
                stdout=DEVNULL,
                stderr=DEVNULL,
            )

            img = next(tdpath.glob("boot*.img"))
            extractor = Path(__file__).absolute().parent / "extract-ikconfig"
            config = await check_call([extractor, img], capture_output=True)
            async with aiofiles.open(output, "wb") as f:
                await f.write(config)
