# Copyright (c) 2024, Oracle and/or/ its affiliates.
# Licensed under the terms of the GNU General Public License.
import json
import urllib.parse
from pathlib import Path
from typing import Mapping
from typing import NamedTuple
from typing import TypedDict
from typing import cast

from kconfigs.distro import DistroConfig
from kconfigs.index import Index
from kconfigs.index import IndexId
from kconfigs.index import alru_cache
from kconfigs.model import JSON
from kconfigs.model import Artifact
from kconfigs.model import IndexState
from kconfigs.util import download_file_mem


class GithubIndexId(NamedTuple):
    owner: str
    repo: str

    def __str__(self) -> str:
        return f"github-{self.owner}-{self.repo}"


class GithubIndexStateData(TypedDict):
    release_id: int | str
    tag_name: str
    tarball_url: str


def github_data(state: IndexState) -> GithubIndexStateData:
    return cast(GithubIndexStateData, state.data)


def _repo_parts(index: str) -> tuple[str, str]:
    owner, repo = urllib.parse.urlparse(index).path.strip("/").split("/")
    return owner, repo


async def _query_latest_release(owner: str, repo: str) -> GithubIndexStateData:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    data = await download_file_mem(url)
    resp = json.loads(data.decode("utf-8"))
    release = resp[0]
    return {
        "release_id": release.get("id", release["tarball_url"]),
        "tag_name": release.get("tag_name", ""),
        "tarball_url": release["tarball_url"],
    }


class GithubIndex(Index):
    def __init__(self, index_id: IndexId, path: Path):
        super().__init__(index_id, path)
        if not isinstance(index_id, GithubIndexId):
            raise TypeError(
                f"{type(self).__name__} requires GithubIndexId, "
                f"not {type(index_id).__name__}"
            )
        self.owner = index_id.owner
        self.repo = index_id.repo

    @classmethod
    def index_id(cls, dc: DistroConfig) -> GithubIndexId:
        return GithubIndexId(*_repo_parts(dc.index))

    @alru_cache
    async def check(self) -> IndexState:
        data = await _query_latest_release(self.owner, self.repo)
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
        data = github_data(state)
        return Artifact(
            url=data["tarball_url"],
            checksum=None,
            signature_url=None,
            source_index_state=state,
            version=data["tag_name"],
        )
