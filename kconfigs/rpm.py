# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import posixpath
import re
import shlex
import shutil
import xml.etree.ElementTree as ET
from asyncio.subprocess import DEVNULL
from asyncio.subprocess import PIPE
from functools import cmp_to_key
from itertools import zip_longest
from pathlib import Path
from typing import Any
from typing import NamedTuple
from typing import TypeVar

import aiosqlite
from aiofiles.tempfile import TemporaryDirectory

from kconfigs.extractor import Extractor
from kconfigs.fetcher import Checksum
from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
from kconfigs.util import check_call
from kconfigs.util import download_file
from kconfigs.util import download_file_mem_verified
from kconfigs.util import maybe_decompress

REPODATA = "repodata/repomd.xml"
GROUPRE = re.compile("([0-9]+|[a-zA-Z]+)")


T = TypeVar("T", str, int)


class PkgMeta(NamedTuple):
    version: str
    release: str
    href: str
    checksum: str
    checksum_type: str


def samekindcmp(s1: T, s2: T) -> int:
    if s1 > s2:
        # s1 is greater, so it's newer
        return 1
    elif s1 < s2:
        return -1
    else:
        return 0


def rpmvercmp(l1: str, l2: str) -> int:
    # https://fedoraproject.org/wiki/Archive:Tools/RPM/VersionComparison#The_rpmvercmp_algorithm
    # return 1 when l1 is newer than l2
    for elem1, elem2 in zip_longest(
        GROUPRE.findall(l1), GROUPRE.findall(l2), fillvalue=None
    ):
        if elem2 is None:
            # l1 is longer, so it is newer
            return 1
        elif elem1 is None:
            # l2 is longer, so it is newer
            return -1
        try:
            i1 = int(elem1)
        except ValueError:
            i1 = None
        try:
            i2 = int(elem2)
        except ValueError:
            i2 = None

        if i1 is None and i2 is None:
            cmp = samekindcmp(elem1, elem2)
        elif i1 is None:
            # l1 is alpha, i2 is numeric, so i2 is newer
            cmp = -1
        elif i2 is None:
            cmp = 1
        else:
            cmp = samekindcmp(i1, i2)
        if cmp != 0:
            return cmp
    return 0


class RpmFetcher(Fetcher):
    def __init__(
        self, saved_data: dict[str, Any], dc: DistroConfig, savedir: Path
    ):
        self.__last_db: None | str = saved_data.get("last_db")
        self.__latest_db: None | str = None
        self.__latest_checksum: None | tuple[str, str] = None
        self.__latest_db_path: None | Path = None
        self.__mutex = asyncio.Lock()
        self.index = dc.index
        self.savedir = savedir
        assert dc.key
        self.key = dc.key

    @classmethod
    def uid(cls, dc: DistroConfig) -> str:
        return dc.index

    def save_data(self) -> dict[str, Any]:
        return {"last_db": self.__latest_db or self.__last_db}

    async def __query_latest_db(self) -> None:
        yum_base = posixpath.join(self.index, REPODATA)
        data = await download_file_mem_verified(
            yum_base, self.key, https_ok=True
        )
        tree = ET.fromstring(data.decode("utf-8"))
        # prefer primary_db because it's sqlite, which is faster to query
        primary_db_list = tree.findall(".//{*}data[@type='primary_db']")
        if not primary_db_list:
            # fall back to XML where necessary
            primary_db_list = tree.findall(".//{*}data[@type='primary']")
        primary_db_data = primary_db_list[0]
        location = primary_db_data.findall("{*}location")[0]
        checksum = primary_db_data.findall("{*}checksum")[0]
        href = location.attrib["href"]
        if not href.startswith("http:") or href.startswith("https:"):
            href = posixpath.join(self.index, href)
        self.__latest_db = href
        assert checksum.text
        self.__latest_checksum = (checksum.attrib["type"], checksum.text)

    async def is_updated(self) -> bool:
        async with self.__mutex:
            if not self.__latest_db:
                await self.__query_latest_db()
            return self.__latest_db != self.__last_db

    async def __fetch_latest_db(self) -> None:
        if not self.__latest_db:
            await self.__query_latest_db()
        assert self.__latest_db
        assert self.__latest_checksum
        name = posixpath.basename(self.__latest_db)
        file = self.savedir / name
        await download_file(
            self.__latest_db, file, checksum=self.__latest_checksum
        )
        self.__latest_db_path = await maybe_decompress(file)

    async def __packages_from_sqlite(self, pkg: str) -> list[PkgMeta]:
        assert self.__latest_db_path
        async with aiosqlite.connect(self.__latest_db_path) as conn:
            result = await conn.execute(
                """
                SELECT version, release, location_href, pkgId, checksum_type FROM packages
                WHERE name=? AND location_href NOT LIKE '%.src.rpm';
                """,
                (pkg,),
            )
            rows = await result.fetchall()
            return [PkgMeta(*row) for row in rows]

    async def __packages_from_xml(self, pkg: str) -> list[PkgMeta]:
        assert self.__latest_db_path
        with open(self.__latest_db_path, "rt") as f:
            tree = ET.parse(f)
        pkgs = tree.findall("{*}package[{*}name='%s']" % pkg)
        res = []
        for pkg_elem in pkgs:
            ver_elem = pkg_elem.find("{*}version")
            csum_elem = pkg_elem.find("{*}checksum")
            loc_elem = pkg_elem.find("{*}location")
            assert (
                ver_elem is not None
                and csum_elem is not None
                and loc_elem is not None
            )
            res.append(
                PkgMeta(
                    ver_elem.attrib["ver"],
                    ver_elem.attrib["rel"],
                    loc_elem.attrib["href"],
                    csum_elem.text or "",  # satisfy mypy here :/
                    csum_elem.attrib["type"],
                )
            )
        return res

    async def latest_version_url(self, pkg: str) -> tuple[str, Checksum | None]:
        async with self.__mutex:
            if not self.__latest_db_path:
                await self.__fetch_latest_db()
            assert self.__latest_db_path

        if self.__latest_db_path.suffix == ".xml":
            rows = await self.__packages_from_xml(pkg)
        else:
            rows = await self.__packages_from_sqlite(pkg)

        def cmp(t1: PkgMeta, t2: PkgMeta) -> int:
            val = rpmvercmp(t1.version, t2.version)
            if val == 0:
                val = rpmvercmp(t1.release, t2.release)
            return val

        rows.sort(key=cmp_to_key(cmp))
        href = rows[-1].href
        csum = rows[-1].checksum
        csum_type = rows[-1].checksum_type
        if not href.startswith("http:") or href.startswith("https:"):
            href = posixpath.join(self.index, href)
        return (href, (csum_type, csum))


async def extract_rpm_file(
    rpm: Path,
    output: Path,
    patterns: list[str],
) -> None:
    filename = shlex.quote(str(rpm))

    async with TemporaryDirectory() as td:
        tdpath = Path(td)
        args = shlex.join(patterns)
        cmd = f"rpm2cpio {filename} | cpio -ivd {args}"
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=td,
            stderr=PIPE,
        )
        _, stderr = await proc.communicate()
        files = stderr.decode("utf-8").strip().split("\n")[:-1]
        if len(files) != 1:
            raise Exception(
                f"multiple files extracted from RPM match pattern: {files}"
            )

        file = tdpath / files[0]
        shutil.copyfile(tdpath / file, output)


# Some GPG "key" names in the "gpg-keys" directory are actually a combination of
# multiple keys into a GPG database. This works for the gpg_verify() function,
# which will happily accept that database and verify using any one of the given
# keys. However, for RPM signature verification, we need to let RPM manage the
# GPG database. This dictionary maps GPG key database names to the underlying
# keys, so we can put them into a temporary RPM key database.
# TODO: generate this from the gpg-keys/Makefile directory?
MULTI_KEYS: dict[str, list[str]] = {
    "asahi.gpg": ["fedora-asahi", "fedora-asahi-kernel"],
}


async def verify_rpm(rpm: Path, key: str) -> None:
    keydir = Path(__file__).parent.parent.resolve() / "gpg-keys"
    if key in MULTI_KEYS:
        key_paths = [keydir / s for s in MULTI_KEYS[key]]
    else:
        key_paths = [keydir / key]
    async with TemporaryDirectory() as td:
        for key_path in key_paths:
            await check_call(
                ["/usr/bin/rpm", f"--dbpath={td}", "--import", key_path]
            )
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/rpm",
            f"--dbpath={td}",
            "-K",
            rpm,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        code = await proc.wait()
        if code == 0:
            print(f"RPM: Good signature [{key}] for {rpm}")
        else:
            raise Exception(f"RPM: Bad signature for {rpm}")


class RpmExtractor(Extractor):
    async def extract_kconfig(
        self, package: Path, output: Path, dc: DistroConfig
    ) -> None:
        assert dc.key
        await verify_rpm(package, dc.key)
        return await extract_rpm_file(
            package, output, ["*/config", "./boot/config-*"]
        )
