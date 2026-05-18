# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import hashlib
import json
import posixpath
import re
import shutil
from pathlib import Path
from typing import Mapping
from typing import NamedTuple
from typing import NotRequired
from typing import TypedDict
from typing import cast

import aiofiles
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
from kconfigs.util import download_file
from kconfigs.util import head_file


class PacmanIndexId(NamedTuple):
    repo_url: str
    repo: str

    def __str__(self) -> str:
        return f"{self.repo_url} repo={self.repo}"


class PacmanIndexStateData(TypedDict):
    db_url: str
    last_modified: str
    etag: NotRequired[str]


def pacman_data(state: IndexState) -> PacmanIndexStateData:
    return cast(PacmanIndexStateData, state.data)


def _db_url(index: str, repo: str) -> str:
    return posixpath.join(index, f"{repo}.db.tar.gz")


def _state_cache_token(state: IndexState) -> str:
    encoded = json.dumps(state.to_json(), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_file_name(url: str, token: str) -> str:
    basename = posixpath.basename(url)
    safe_basename = re.sub(r"[^A-Za-z0-9_.-]", "_", basename)
    return f"{token}-{safe_basename}"


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


class PacmanIndex(Index):
    def __init__(self, index_id: IndexId, path: Path):
        super().__init__(index_id, path)
        if not isinstance(index_id, PacmanIndexId):
            raise TypeError(
                f"{type(self).__name__} requires PacmanIndexId, "
                f"not {type(index_id).__name__}"
            )
        self.index = index_id.repo_url
        self.repo = index_id.repo
        self.dburl = _db_url(self.index, self.repo)
        self.savedir = path
        self.savedir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def index_id(cls, dc: DistroConfig) -> PacmanIndexId:
        assert dc.repo is not None
        return PacmanIndexId(dc.index, dc.repo)

    @alru_cache
    async def check(self) -> IndexState:
        headers = await head_file(self.dburl)
        data: PacmanIndexStateData = {
            "db_url": self.dburl,
            "last_modified": headers["Last-Modified"],
        }
        etag = headers.get("ETag")
        if etag:
            data["etag"] = etag
        return IndexState(
            self.name, str(self.id), cast(Mapping[str, JSON], data)
        )

    @alru_cache
    async def _materialized_db(self) -> Path:
        state = await self.check()
        data = pacman_data(state)
        token = _state_cache_token(state)
        dbpath = self.savedir / _cache_file_name(data["db_url"], token)
        extract_dir = self.savedir / f"{token}-db"
        if not extract_dir.exists():
            await download_file(data["db_url"], dbpath)
            tmp_dir = self.savedir / f"{token}-db.tmp"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            tmp_dir.mkdir()
            await check_call(["tar", "xf", dbpath], cwd=tmp_dir)
            tmp_dir.rename(extract_dir)
        return extract_dir

    async def resolve(self, dc: DistroConfig) -> Artifact:
        if self.index_id(dc) != self.id:
            raise ValueError(
                f"{type(self).__name__}.resolve() got distro for "
                f"{self.index_id(dc)}, expected {self.id}"
            )
        state = await self.check()
        db_dir = await self._materialized_db()
        for package_dir in db_dir.glob(f"{dc.package}-*"):
            desc = await parse_desc(package_dir / "desc")
            if desc["NAME"] == dc.package:
                break
        else:
            raise Exception(f"could not find package: {dc.package}")
        checksum = ("sha256", desc["SHA256SUM"])
        url = posixpath.join(self.index, desc["FILENAME"])
        return Artifact(
            url=url,
            checksum=checksum,
            signature_url=url + ".sig",
            source_index_state=state,
            version=desc["VERSION"],
        )
