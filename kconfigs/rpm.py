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
from typing import cast
from typing import TypeVar

import aiosqlite
from aiofiles.tempfile import TemporaryDirectory
from aiosqlite import Row

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
        primary_db_data = tree.findall(".//{*}data[@type='primary_db']")[0]
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

    async def latest_version_url(self, pkg: str) -> tuple[str, Checksum | None]:
        async with self.__mutex:
            if not self.__latest_db_path:
                await self.__fetch_latest_db()
            assert self.__latest_db_path

        async with aiosqlite.connect(self.__latest_db_path) as conn:
            result = await conn.execute(
                """
                SELECT version, release, location_href, pkgId, checksum_type FROM packages
                WHERE name=? AND location_href NOT LIKE '%.src.rpm';
                """,
                (pkg,),
            )
            rows = list(await result.fetchall())

        def cmp(t1: Row, t2: Row) -> int:
            val = rpmvercmp(t1[0], t2[0])
            if val == 0:
                val = rpmvercmp(t1[1], t2[1])
            return val

        rows.sort(key=cmp_to_key(cmp))
        href = cast(str, rows[-1][2])
        csum = cast(str, rows[-1][3])
        csum_type = cast(str, rows[-1][4])
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


async def verify_rpm(rpm: Path, key: str) -> None:
    key_path = Path(__file__).parent.parent.resolve() / f"gpg-keys/{key}"
    async with TemporaryDirectory() as td:
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
