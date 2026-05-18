# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import re
from asyncio.subprocess import DEVNULL
from pathlib import Path
from typing import Any
from typing import Mapping
from typing import TypedDict
from typing import cast

import aiofiles
from aiofiles.tempfile import TemporaryDirectory

from kconfigs.extractor import Extractor
from kconfigs.fetcher import Checksum
from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
from kconfigs.index import Index
from kconfigs.index import IndexId
from kconfigs.index import alru_cache
from kconfigs.model import JSON
from kconfigs.model import Artifact
from kconfigs.model import IndexState
from kconfigs.util import check_call
from kconfigs.util import download_file_mem


class AndroidGkiIndexStateData(TypedDict):
    url: str
    year: int
    month: int
    revision: int
    version: str


def android_gki_data(state: IndexState) -> AndroidGkiIndexStateData:
    return cast(AndroidGkiIndexStateData, state.data)


def _latest_gki_link(page: str) -> AndroidGkiIndexStateData:
    expr = re.compile(
        r"https://[^\s\"'<>]+gki-certified-boot-android\d+-\d+\.\d+-\d{4}-\d{2}_r\d+\.zip"
    )
    # The names are like: android12-5.10-2023-03_r3.zip. These almost
    # naturally sort alphanumerically, but not quite. The prefix
    # (android12-5.10) is constant, and only the YYYY-MM_rX value changes.
    # However that X may be single or double digit, so we parse it.
    links: list[str] = list(set(expr.findall(page)))
    verexpr = re.compile(r"^.*(\d{4})-(\d{2})_r(\d+)\.zip$")

    def key_fn(link: str) -> tuple[int, int, int]:
        m = verexpr.fullmatch(link)
        assert m
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))

    links.sort(key=key_fn)
    latest = links[-1]
    m = verexpr.fullmatch(latest)
    assert m
    year = int(m.group(1))
    month = int(m.group(2))
    revision = int(m.group(3))
    return {
        "url": latest,
        "year": year,
        "month": month,
        "revision": revision,
        "version": f"{year}-{month:02d}_r{revision}",
    }


async def _query_latest_gki(index: str) -> AndroidGkiIndexStateData:
    return _latest_gki_link((await download_file_mem(index)).decode("utf-8"))


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
        data = await _query_latest_gki(self.index)
        return (data["url"], None)


class AndroidGkiIndex(Index):
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
        data = await _query_latest_gki(self.index)
        return IndexState(
            self.name, str(self.id), cast(Mapping[str, JSON], data)
        )

    async def resolve(self, dc: DistroConfig) -> Artifact:
        if self.index_id(dc) != self.id:
            raise ValueError(
                f"{type(self).__name__}.resolve() got distro for "
                f"{self.index_id(dc)}, expected {self.id}"
            )
        state = await self.check()
        data = android_gki_data(state)
        return Artifact(
            url=data["url"],
            checksum=None,
            signature_url=None,
            source_index_state=state,
            version=data["version"],
        )


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
