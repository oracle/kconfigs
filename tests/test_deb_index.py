# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import shutil
from pathlib import Path

import pytest

import kconfigs.deb as deb_module
from kconfigs.deb import DebIndex
from kconfigs.fetcher import DistroConfig
from kconfigs.model import Checksum
from kconfigs.model import IndexState


def make_distro(
    *,
    package: str = "linux-generic",
    name: str = "Ubuntu Test",
) -> DistroConfig:
    return DistroConfig(
        name=name,
        version="24.04 LTS Noble",
        arch="x86_64",
        package=package,
        fetcher="kconfigs.deb.DebIndex",
        extractor="kconfigs.deb.DebExtractor",
        index="http://archive.example.com/ubuntu/",
        codename="noble",
        key="ubuntu",
    )


def test_deb_index_check_prefers_xz_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None, str]] = []
    release = b"""
Origin: Ubuntu
SHA256:
 old-hash 1 main/binary-amd64/Packages.gz
 new-hash 1 main/binary-amd64/Packages.xz
 other-hash 1 universe/binary-amd64/Packages.xz
SHA512:
 ignored 1 main/binary-amd64/Packages.xz
"""

    async def download_file_mem_verified(
        url: str,
        key: str | None,
        https_ok: bool = False,
        suffix: str = ".asc",
    ) -> bytes:
        del https_ok
        calls.append((url, key, suffix))
        return release

    monkeypatch.setattr(
        deb_module, "download_file_mem_verified", download_file_mem_verified
    )

    dc = make_distro()
    index = DebIndex(DebIndex.index_id(dc), tmp_path)

    state = asyncio.run(index.check())

    assert calls == [
        (
            "http://archive.example.com/ubuntu/dists/noble/Release",
            "ubuntu",
            ".gpg",
        )
    ]
    assert state == IndexState(
        "kconfigs.deb.DebIndex",
        str(DebIndex.index_id(dc)),
        {
            "packages_path": "main/binary-amd64/Packages.xz",
            "checksum": ("sha256", "new-hash"),
        },
    )


def test_deb_index_resolve_materializes_packages_once_for_shared_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packages = tmp_path / "Packages"
    packages.write_text(
        """
Package: linux-image-generic
Version: 1.0
Depends: linux-image-6.8.0-31-generic (= 6.8.0-31.31), other-package
Filename: pool/main/l/linux-meta/linux-image-generic.deb
SHA256: meta-checksum

Package: linux-image-6.8.0-31-generic
Version: 6.8.0-31.31
Filename: pool/main/l/linux/linux-image-6.8.0-31-generic.deb
SHA256: image-checksum

Package: linux-modules-6.8.0-31-generic
Version: 6.8.0-31.31
Filename: pool/main/l/linux/linux-modules-6.8.0-31-generic.deb
SHA256: modules-checksum

Package: linux-image-lowlatency
Version: 1.0
Depends: linux-image-6.8.0-31-lowlatency (= 6.8.0-31.31)
Filename: pool/main/l/linux-meta/linux-image-lowlatency.deb
SHA256: lowlatency-meta

Package: linux-image-6.8.0-31-lowlatency
Version: 6.8.0-31.31
Filename: pool/main/l/linux/linux-image-6.8.0-31-lowlatency.deb
SHA256: lowlatency-image
"""
    )
    release = b"""
Origin: Ubuntu
SHA256:
 packages-checksum 1 main/binary-amd64/Packages.xz
"""
    check_calls: list[str] = []
    materialize_calls: list[tuple[str, Path, Checksum | None]] = []

    async def download_file_mem_verified(
        url: str,
        key: str | None,
        https_ok: bool = False,
        suffix: str = ".asc",
    ) -> bytes:
        del key, https_ok, suffix
        check_calls.append(url)
        await asyncio.sleep(0)
        return release

    async def download_file(
        url: str,
        file: Path,
        always_download: bool = False,
        checksum: Checksum | None = None,
    ) -> None:
        del always_download
        materialize_calls.append((url, file, checksum))
        await asyncio.sleep(0)
        shutil.copyfile(packages, file)

    async def maybe_decompress(file: Path) -> Path:
        return file

    monkeypatch.setattr(
        deb_module, "download_file_mem_verified", download_file_mem_verified
    )
    monkeypatch.setattr(deb_module, "download_file", download_file)
    monkeypatch.setattr(deb_module, "maybe_decompress", maybe_decompress)

    dc = make_distro()
    dc_lowlatency = make_distro(
        package="linux-lowlatency", name="Ubuntu Lowlatency Test"
    )
    index = DebIndex(DebIndex.index_id(dc), tmp_path / "cache")
    index_state = IndexState(
        "kconfigs.deb.DebIndex",
        str(DebIndex.index_id(dc)),
        {
            "packages_path": "main/binary-amd64/Packages.xz",
            "checksum": ("sha256", "packages-checksum"),
        },
    )

    async def exercise() -> None:
        artifact, lowlatency_artifact = await asyncio.gather(
            index.resolve(dc),
            index.resolve(dc_lowlatency),
        )

        assert (
            artifact.url
            == "http://archive.example.com/ubuntu/pool/main/l/linux/linux-modules-6.8.0-31-generic.deb"
        )
        assert artifact.checksum == ("sha256", "modules-checksum")
        assert artifact.signature_url is None
        assert artifact.source_index_state == index_state
        assert artifact.version == "6.8.0-31.31"

        assert (
            lowlatency_artifact.url
            == "http://archive.example.com/ubuntu/pool/main/l/linux/linux-image-6.8.0-31-lowlatency.deb"
        )
        assert lowlatency_artifact.checksum == ("sha256", "lowlatency-image")
        assert lowlatency_artifact.source_index_state == index_state
        assert lowlatency_artifact.version == "6.8.0-31.31"

    asyncio.run(exercise())

    assert check_calls == [
        "http://archive.example.com/ubuntu/dists/noble/Release"
    ]
    assert len(materialize_calls) == 1
    assert (
        materialize_calls[0][0]
        == "http://archive.example.com/ubuntu/dists/noble/main/binary-amd64/Packages.xz"
    )
    assert materialize_calls[0][2] == ("sha256", "packages-checksum")
