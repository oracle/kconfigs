# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import tarfile
from pathlib import Path

import pytest

import kconfigs.pacman as pacman_module
from kconfigs.distro import DistroConfig
from kconfigs.model import Checksum
from kconfigs.model import IndexState
from kconfigs.pacman import PacmanIndex


def make_distro(
    *,
    package: str = "linux",
    name: str = "Arch Test",
) -> DistroConfig:
    return DistroConfig(
        name=name,
        arch="x86_64",
        package=package,
        fetcher="kconfigs.pacman.PacmanIndex",
        extractor="kconfigs.pacman.PacmanExtractor",
        index="https://mirror.example.com/archlinux/core/os/x86_64/",
        repo="core",
        key="archlinux",
    )


def make_repo_db(path: Path) -> None:
    root = path.parent / "repo-db"
    package_dir = root / "linux-6.9.arch1-1"
    package_dir.mkdir(parents=True)
    (package_dir / "desc").write_text(
        """
%NAME%
linux

%VERSION%
6.9.arch1-1

%FILENAME%
linux-6.9.arch1-1-x86_64.pkg.tar.zst

%SHA256SUM%
package-checksum
""".lstrip()
    )
    with tarfile.open(path, "w:gz") as tar:
        tar.add(package_dir, arcname=package_dir.name)


def test_pacman_index_check_uses_repo_db_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def head_file(url: str) -> dict[str, str]:
        calls.append(url)
        return {
            "Last-Modified": "Fri, 01 May 2026 00:00:00 GMT",
            "ETag": '"repo-etag"',
        }

    monkeypatch.setattr(pacman_module, "head_file", head_file)

    dc = make_distro()
    index = PacmanIndex(PacmanIndex.index_id(dc), tmp_path)

    state = asyncio.run(index.check())

    assert calls == [
        "https://mirror.example.com/archlinux/core/os/x86_64/core.db.tar.gz"
    ]
    assert state == IndexState(
        "kconfigs.pacman.PacmanIndex",
        str(PacmanIndex.index_id(dc)),
        {
            "db_url": "https://mirror.example.com/archlinux/core/os/x86_64/core.db.tar.gz",
            "last_modified": "Fri, 01 May 2026 00:00:00 GMT",
            "etag": '"repo-etag"',
        },
    )


def test_pacman_index_resolve_materializes_db_once_for_shared_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_db = tmp_path / "core.db.tar.gz"
    make_repo_db(repo_db)
    check_calls: list[str] = []
    materialize_calls: list[tuple[str, Path, Checksum | None]] = []

    async def head_file(url: str) -> dict[str, str]:
        check_calls.append(url)
        await asyncio.sleep(0)
        return {"Last-Modified": "Fri, 01 May 2026 00:00:00 GMT"}

    async def download_file(
        url: str,
        file: Path,
        always_download: bool = False,
        checksum: Checksum | None = None,
    ) -> None:
        del always_download
        materialize_calls.append((url, file, checksum))
        await asyncio.sleep(0)
        file.write_bytes(repo_db.read_bytes())

    monkeypatch.setattr(pacman_module, "head_file", head_file)
    monkeypatch.setattr(pacman_module, "download_file", download_file)

    dc = make_distro()
    index = PacmanIndex(PacmanIndex.index_id(dc), tmp_path / "cache")
    index_state = IndexState(
        "kconfigs.pacman.PacmanIndex",
        str(PacmanIndex.index_id(dc)),
        {
            "db_url": "https://mirror.example.com/archlinux/core/os/x86_64/core.db.tar.gz",
            "last_modified": "Fri, 01 May 2026 00:00:00 GMT",
        },
    )

    async def exercise() -> None:
        artifact, same_artifact = await asyncio.gather(
            index.resolve(dc),
            index.resolve(dc),
        )

        for resolved in (artifact, same_artifact):
            assert (
                resolved.url
                == "https://mirror.example.com/archlinux/core/os/x86_64/linux-6.9.arch1-1-x86_64.pkg.tar.zst"
            )
            assert resolved.checksum == ("sha256", "package-checksum")
            assert (
                resolved.signature_url
                == "https://mirror.example.com/archlinux/core/os/x86_64/linux-6.9.arch1-1-x86_64.pkg.tar.zst.sig"
            )
            assert resolved.source_index_state == index_state
            assert resolved.version == "6.9.arch1-1"

    asyncio.run(exercise())

    assert check_calls == [
        "https://mirror.example.com/archlinux/core/os/x86_64/core.db.tar.gz"
    ]
    assert len(materialize_calls) == 1
    assert materialize_calls[0][0] == (
        "https://mirror.example.com/archlinux/core/os/x86_64/core.db.tar.gz"
    )
    assert materialize_calls[0][2] is None
