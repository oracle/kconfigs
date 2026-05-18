# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import json
from pathlib import Path

import pytest

import kconfigs.android as android_module
import kconfigs.github as github_module
import kconfigs.upstream as upstream_module
from kconfigs.android import AndroidGkiIndex
from kconfigs.fetcher import DistroConfig
from kconfigs.github import GithubIndex
from kconfigs.model import IndexState
from kconfigs.upstream import UpstreamIndex


def make_upstream_distro(
    *,
    version: str = "6.1",
    arch: str = "x86_64",
) -> DistroConfig:
    return DistroConfig(
        name="Upstream Test",
        version=version,
        arch=arch,
        package="linux",
        fetcher="kconfigs.upstream.UpstreamIndex",
        extractor="kconfigs.upstream.DefconfigExtractor",
        index="https://kernel.example.com/feeds/kdist.xml",
        key="gregkh",
    )


def test_upstream_index_resolves_release_lines_from_shared_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    feed = b"""
<rss>
  <channel>
    <item>
      <title>6.10.1: stable</title>
      <description>https://cdn.example.com/linux-6.10.1.tar.xz</description>
    </item>
    <item>
      <title>6.1.99: stable</title>
      <description>https://cdn.example.com/linux-6.1.99.tar.xz</description>
    </item>
  </channel>
</rss>
"""

    async def download_file_mem(
        url: str, checksum: tuple[str, str] | None = None
    ) -> bytes:
        del checksum
        calls.append(url)
        await asyncio.sleep(0)
        return feed

    monkeypatch.setattr(upstream_module, "download_file_mem", download_file_mem)

    dc_6_1 = make_upstream_distro(version="6.1")
    dc_6_10 = make_upstream_distro(version="6.10", arch="aarch64")
    index = UpstreamIndex(UpstreamIndex.index_id(dc_6_1), tmp_path)
    index_state = IndexState(
        "kconfigs.upstream.UpstreamIndex",
        "https://kernel.example.com/feeds/kdist.xml",
        {
            "kernels": (
                {
                    "version": "6.10.1",
                    "url": "https://cdn.example.com/linux-6.10.1.tar.xz",
                },
                {
                    "version": "6.1.99",
                    "url": "https://cdn.example.com/linux-6.1.99.tar.xz",
                },
            )
        },
    )

    async def exercise() -> None:
        artifact_6_1, artifact_6_10 = await asyncio.gather(
            index.resolve(dc_6_1),
            index.resolve(dc_6_10),
        )

        assert artifact_6_1.url == "https://cdn.example.com/linux-6.1.99.tar.xz"
        assert artifact_6_1.checksum is None
        assert (
            artifact_6_1.signature_url
            == "https://cdn.example.com/linux-6.1.99.tar.sign"
        )
        assert artifact_6_1.source_index_state == index_state
        assert artifact_6_1.version == "6.1.99"

        assert (
            artifact_6_10.url == "https://cdn.example.com/linux-6.10.1.tar.xz"
        )
        assert artifact_6_10.source_index_state == index_state
        assert artifact_6_10.version == "6.10.1"

    asyncio.run(exercise())

    assert calls == ["https://kernel.example.com/feeds/kdist.xml"]


def make_github_distro() -> DistroConfig:
    return DistroConfig(
        name="GitHub Test",
        version="4.14",
        arch="x86_64",
        package="linux",
        fetcher="kconfigs.github.GithubIndex",
        extractor="kconfigs.upstream.DefconfigExtractor",
        index="https://github.com/openela/kernel-lts",
        key="NOVERIFY-GITHUB",
    )


def test_github_index_wraps_latest_release_tarball(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    releases = [
        {
            "id": 42,
            "tag_name": "v4.14.350-openela",
            "tarball_url": "https://api.github.com/repos/openela/kernel-lts/tarball/v4.14.350-openela",
        }
    ]

    async def download_file_mem(
        url: str, checksum: tuple[str, str] | None = None
    ) -> bytes:
        del checksum
        calls.append(url)
        return json.dumps(releases).encode("utf-8")

    monkeypatch.setattr(github_module, "download_file_mem", download_file_mem)

    dc = make_github_distro()
    index = GithubIndex(GithubIndex.index_id(dc), tmp_path)
    artifact = asyncio.run(index.resolve(dc))

    assert calls == ["https://api.github.com/repos/openela/kernel-lts/releases"]
    assert artifact.url == releases[0]["tarball_url"]
    assert artifact.checksum is None
    assert artifact.signature_url is None
    assert artifact.source_index_state == IndexState(
        "kconfigs.github.GithubIndex",
        "github-openela-kernel-lts",
        {
            "release_id": 42,
            "tag_name": "v4.14.350-openela",
            "tarball_url": releases[0]["tarball_url"],
        },
    )
    assert artifact.version == "v4.14.350-openela"


def make_android_distro() -> DistroConfig:
    return DistroConfig(
        name="Android Test",
        version="15 (6.6)",
        arch="aarch64",
        package="linux",
        fetcher="kconfigs.android.AndroidGkiIndex",
        extractor="kconfigs.android.AndroidGkiExtractor",
        index="https://source.example.com/gki-android15-6_6-release-builds",
    )


def test_android_gki_index_selects_latest_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    page = b"""
<a href="https://dl.example.com/gki-certified-boot-android15-6.6-2026-04_r9.zip">old</a>
<a href="https://dl.example.com/gki-certified-boot-android15-6.6-2026-04_r10.zip">new</a>
"""

    async def download_file_mem(
        url: str, checksum: tuple[str, str] | None = None
    ) -> bytes:
        del checksum
        calls.append(url)
        return page

    monkeypatch.setattr(android_module, "download_file_mem", download_file_mem)

    dc = make_android_distro()
    index = AndroidGkiIndex(AndroidGkiIndex.index_id(dc), tmp_path)
    artifact = asyncio.run(index.resolve(dc))

    assert calls == [
        "https://source.example.com/gki-android15-6_6-release-builds"
    ]
    assert (
        artifact.url
        == "https://dl.example.com/gki-certified-boot-android15-6.6-2026-04_r10.zip"
    )
    assert artifact.checksum is None
    assert artifact.signature_url is None
    assert artifact.source_index_state == IndexState(
        "kconfigs.android.AndroidGkiIndex",
        "https://source.example.com/gki-android15-6_6-release-builds",
        {
            "url": "https://dl.example.com/gki-certified-boot-android15-6.6-2026-04_r10.zip",
            "year": 2026,
            "month": 4,
            "revision": 10,
            "version": "2026-04_r10",
        },
    )
    assert artifact.version == "2026-04_r10"
