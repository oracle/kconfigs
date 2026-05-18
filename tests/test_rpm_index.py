# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import shutil
import sqlite3
from pathlib import Path

import pytest

import kconfigs.rpm as rpm_module
from kconfigs.distro import DistroConfig
from kconfigs.model import Checksum
from kconfigs.model import IndexState
from kconfigs.rpm import RpmIndex


def make_distro(
    *,
    package: str = "kernel-uek-core",
    name: str = "Oracle Linux Test",
) -> DistroConfig:
    return DistroConfig(
        name=name,
        version="10 (UEK 8)",
        arch="aarch64",
        package=package,
        fetcher="kconfigs.rpm.RpmIndex",
        extractor="kconfigs.rpm.RpmExtractor",
        index="https://yum.example.com/repo/OracleLinux/OL10/UEKR8/aarch64/",
        key="RPM-GPG-KEY-oracle-ol10",
    )


def make_sqlite_primary(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE packages (
                name TEXT,
                version TEXT,
                release TEXT,
                location_href TEXT,
                pkgId TEXT,
                checksum_type TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO packages (
                name, version, release, location_href, pkgId, checksum_type
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "kernel-uek-core",
                    "1.0",
                    "1.el10",
                    "Packages/kernel-uek-core-1.rpm",
                    "old-package",
                    "sha256",
                ),
                (
                    "kernel-uek-core",
                    "9.0",
                    "1.el10",
                    "Packages/kernel-uek-core-9.src.rpm",
                    "source-package",
                    "sha256",
                ),
                (
                    "kernel-uek-core",
                    "2.0",
                    "3.el10",
                    "Packages/kernel-uek-core-2.rpm",
                    "new-package",
                    "sha256",
                ),
                (
                    "kernel-uek64k-core",
                    "2.0",
                    "1.el10",
                    "Packages/kernel-uek64k-core-2.rpm",
                    "64k-package",
                    "sha256",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def test_rpm_index_check_prefers_primary_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None, bool]] = []
    repomd = b"""
    <repomd xmlns="http://linux.duke.edu/metadata/repo">
      <data type="primary">
        <checksum type="sha256">xml-checksum</checksum>
        <location href="repodata/primary.xml.gz"/>
      </data>
      <data type="primary_db">
        <checksum type="sha256">sqlite-checksum</checksum>
        <location href="repodata/primary.sqlite.bz2"/>
      </data>
    </repomd>
    """

    async def download_file_mem_verified(
        url: str,
        key: str | None,
        https_ok: bool = False,
        suffix: str = ".asc",
    ) -> bytes:
        del suffix
        calls.append((url, key, https_ok))
        return repomd

    monkeypatch.setattr(
        rpm_module, "download_file_mem_verified", download_file_mem_verified
    )

    dc = make_distro()
    index = RpmIndex(RpmIndex.index_id(dc), tmp_path)

    state = asyncio.run(index.check())

    assert calls == [
        (
            "https://yum.example.com/repo/OracleLinux/OL10/UEKR8/aarch64/repodata/repomd.xml",
            "RPM-GPG-KEY-oracle-ol10",
            True,
        )
    ]
    assert state == IndexState(
        "kconfigs.rpm.RpmIndex",
        str(RpmIndex.index_id(dc)),
        {
            "primary_url": "https://yum.example.com/repo/OracleLinux/OL10/UEKR8/aarch64/repodata/primary.sqlite.bz2",
            "checksum": ("sha256", "sqlite-checksum"),
        },
    )


def test_rpm_index_resolve_materializes_sqlite_once_for_shared_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_db = tmp_path / "primary.sqlite"
    make_sqlite_primary(primary_db)
    check_calls: list[str] = []
    materialize_calls: list[tuple[str, Path, Checksum | None]] = []
    dc_path = "repodata/primary.sqlite"
    repomd = f"""
    <repomd xmlns="http://linux.duke.edu/metadata/repo">
      <data type="primary_db">
        <checksum type="sha256">primary-checksum</checksum>
        <location href="{dc_path}"/>
      </data>
    </repomd>
    """.encode()

    async def download_file_mem_verified(
        url: str,
        key: str | None,
        https_ok: bool = False,
        suffix: str = ".asc",
    ) -> bytes:
        del key, https_ok, suffix
        check_calls.append(url)
        await asyncio.sleep(0)
        return repomd

    async def download_file(
        url: str,
        file: Path,
        always_download: bool = False,
        checksum: Checksum | None = None,
    ) -> None:
        del always_download
        materialize_calls.append((url, file, checksum))
        await asyncio.sleep(0)
        shutil.copyfile(primary_db, file)

    monkeypatch.setattr(
        rpm_module, "download_file_mem_verified", download_file_mem_verified
    )
    monkeypatch.setattr(rpm_module, "download_file", download_file)

    dc = make_distro()
    dc_64k = make_distro(
        package="kernel-uek64k-core", name="Oracle Linux Test 64k"
    )
    index = RpmIndex(RpmIndex.index_id(dc), tmp_path / "cache")
    index_state = IndexState(
        "kconfigs.rpm.RpmIndex",
        str(RpmIndex.index_id(dc)),
        {
            "primary_url": f"{dc.index}{dc_path}",
            "checksum": ("sha256", "primary-checksum"),
        },
    )

    async def exercise() -> None:
        artifact, artifact_64k = await asyncio.gather(
            index.resolve(dc),
            index.resolve(dc_64k),
        )

        assert artifact.url == f"{dc.index}Packages/kernel-uek-core-2.rpm"
        assert artifact.checksum == ("sha256", "new-package")
        assert artifact.signature_url is None
        assert artifact.source_index_state == index_state
        assert artifact.version == "2.0-3.el10"

        assert (
            artifact_64k.url == f"{dc.index}Packages/kernel-uek64k-core-2.rpm"
        )
        assert artifact_64k.checksum == ("sha256", "64k-package")
        assert artifact_64k.source_index_state == index_state
        assert artifact_64k.version == "2.0-1.el10"

    asyncio.run(exercise())

    assert check_calls == [f"{dc.index}repodata/repomd.xml"]
    assert len(materialize_calls) == 1
    assert materialize_calls[0][0] == f"{dc.index}repodata/primary.sqlite"
    assert materialize_calls[0][2] == ("sha256", "primary-checksum")


def test_rpm_index_resolve_parses_xml_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary_xml = tmp_path / "primary.xml"
    primary_xml.write_text(
        """
        <metadata xmlns="http://linux.duke.edu/metadata/common">
          <package type="rpm">
            <name>kernel-core</name>
            <version ver="1.0" rel="1.el10"/>
            <checksum type="sha256">old-package</checksum>
            <location href="Packages/kernel-core-1.rpm"/>
          </package>
          <package type="rpm">
            <name>kernel-core</name>
            <version ver="1.0" rel="2.el10"/>
            <checksum type="sha256">new-package</checksum>
            <location href="https://cdn.example.com/kernel-core-1.0-2.rpm"/>
          </package>
        </metadata>
        """
    )
    repomd = b"""
    <repomd xmlns="http://linux.duke.edu/metadata/repo">
      <data type="primary">
        <checksum type="sha256">xml-checksum</checksum>
        <location href="repodata/primary.xml"/>
      </data>
    </repomd>
    """

    async def download_file_mem_verified(
        url: str,
        key: str | None,
        https_ok: bool = False,
        suffix: str = ".asc",
    ) -> bytes:
        del url, key, https_ok, suffix
        return repomd

    async def download_file(
        url: str,
        file: Path,
        always_download: bool = False,
        checksum: Checksum | None = None,
    ) -> None:
        del url, always_download, checksum
        shutil.copyfile(primary_xml, file)

    monkeypatch.setattr(
        rpm_module, "download_file_mem_verified", download_file_mem_verified
    )
    monkeypatch.setattr(rpm_module, "download_file", download_file)

    dc = make_distro(package="kernel-core")
    index = RpmIndex(RpmIndex.index_id(dc), tmp_path / "cache")
    index_state = IndexState(
        "kconfigs.rpm.RpmIndex",
        str(RpmIndex.index_id(dc)),
        {
            "primary_url": f"{dc.index}repodata/primary.xml",
            "checksum": ("sha256", "xml-checksum"),
        },
    )

    artifact = asyncio.run(index.resolve(dc))

    assert artifact.url == "https://cdn.example.com/kernel-core-1.0-2.rpm"
    assert artifact.checksum == ("sha256", "new-package")
    assert artifact.signature_url is None
    assert artifact.source_index_state == index_state
    assert artifact.version == "1.0-2.el10"
