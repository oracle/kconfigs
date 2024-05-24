# Copyright (c) 2024, Oracle and/or/ its affiliates.
# Licensed under the terms of the GNU General Public License.
import json
import urllib.parse
from pathlib import Path
from typing import Any

from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
from kconfigs.util import download_file_mem


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
        self.user, self.repo = (
            urllib.parse.urlparse(dc.index).path.strip("/").split("/")
        )

    def save_data(self) -> dict[str, Any]:
        return {}

    @classmethod
    def uid(cls, dc: DistroConfig) -> str:
        user, repo = urllib.parse.urlparse(dc.index).path.strip("/").split("/")
        return f"github-{user}-{repo}"

    async def is_updated(self) -> bool:
        return True  # there's no extra index to check

    async def latest_version_url(self, package: str) -> tuple[str, None]:
        url = f"https://api.github.com/repos/{self.user}/{self.repo}/releases"
        data = await download_file_mem(url)
        resp = json.loads(data.decode("utf-8"))
        return resp[0]["tarball_url"], None
