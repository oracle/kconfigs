# Copyright (c) 2024, Oracle and/or/ its affiliates.
# Licensed under the terms of the GNU General Public License.
import json
import urllib.parse
from pathlib import Path
from typing import Any
from typing import Mapping
from typing import NamedTuple
from typing import TypedDict
from typing import cast

from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
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


class GithubFetcher(Fetcher):
    """
    Fetcher for Github releases.

    Uses the Github API to fetch the latest release tarball asset. From there,
    you can then use the DefconfigExtractor to generate the default config for
    various architectures.

    To use this, the DistroConfig should set the "index" configuration to be the
    repository URL. You'll also want to set the "key" to "NOVERIFY-GITHUB" so
    that the DefconfigExtractor will not try to verify a non-existing GPG
    signature.
    """

    def __init__(
        self, saved_state: dict[str, Any], dc: DistroConfig, savedir: Path
    ):
        self.user, self.repo = _repo_parts(dc.index)

    def save_data(self) -> dict[str, Any]:
        return {}

    @classmethod
    def uid(cls, dc: DistroConfig) -> str:
        user, repo = _repo_parts(dc.index)
        return f"github-{user}-{repo}"

    async def is_updated(self) -> bool:
        return True  # there's no extra index to check

    async def latest_version_url(self, package: str) -> tuple[str, None]:
        data = await _query_latest_release(self.user, self.repo)
        return data["tarball_url"], None


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
