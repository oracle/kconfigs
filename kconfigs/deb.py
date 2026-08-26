# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import posixpath
import re
import shutil
from pathlib import Path
from typing import Mapping
from typing import NamedTuple
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
from kconfigs.model import Checksum
from kconfigs.model import IndexState
from kconfigs.util import download_file
from kconfigs.util import download_file_mem_verified
from kconfigs.util import maybe_decompress

RPM_TO_DEB_ARCH = {
    "x86_64": "amd64",
    "aarch64": "arm64",
}


class DebIndexId(NamedTuple):
    repo_url: str
    codename: str
    arch: str
    category: str
    key: str

    def __str__(self) -> str:
        return (
            f"{self.repo_url} dists/{self.codename}/{self.category}/"
            f"binary-{self.arch} key={self.key}"
        )


class DebIndexStateData(TypedDict):
    packages_path: str
    checksum: Checksum


def deb_data(state: IndexState) -> DebIndexStateData:
    return cast(DebIndexStateData, state.data)


def _deb_arch(arch: str) -> str:
    return RPM_TO_DEB_ARCH.get(arch, arch)


def _parse_release_hashes(data: str, section: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    in_section = False
    for line in data.splitlines():
        if line == f"{section}:":
            in_section = True
            continue
        if not in_section:
            continue
        if not line.startswith(" "):
            break
        parts = line.split()
        if len(parts) >= 3:
            entries[parts[2]] = parts[0]
    return entries


async def _query_packages_metadata(
    index: str,
    key: str,
    codename: str,
    category: str,
    arch: str,
) -> DebIndexStateData:
    url = posixpath.join(index, "dists", codename, "Release")
    data_bytes = await download_file_mem_verified(url, key, suffix=".gpg")
    file_to_hash = _parse_release_hashes(data_bytes.decode("utf-8"), "SHA256")
    desired_entries = [
        f"{category}/binary-{arch}/Packages.xz",
        f"{category}/binary-{arch}/Packages.bz2",
        f"{category}/binary-{arch}/Packages.gz",
    ]
    for file in desired_entries:
        if file in file_to_hash:
            return {
                "packages_path": file,
                "checksum": ("sha256", file_to_hash[file]),
            }
    raise Exception("Could not find Packages file")


def _metadata_cache_path(
    savedir: Path, packages_path: str, checksum: Checksum
) -> Path:
    name = posixpath.basename(packages_path)
    checksum_name = re.sub(r"[^A-Za-z0-9_.-]", "_", "-".join(checksum))
    return savedir / f"{checksum_name}-{name}"


def _parse_package_stanzas(data: str) -> list[dict[str, str]]:
    stanzas: list[dict[str, str]] = []
    stanza: dict[str, str] = {}
    current_key: str | None = None
    for line in data.splitlines():
        if not line:
            if stanza:
                stanzas.append(stanza)
            stanza = {}
            current_key = None
            continue
        if line[0].isspace():
            if current_key is None:
                raise Exception("Malformed Packages stanza continuation")
            stanza[current_key] += "\n" + line.strip()
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise Exception(f"Malformed Packages line: {line}")
        current_key = key
        stanza[key] = value.strip()
    if stanza:
        stanzas.append(stanza)
    return stanzas


async def _get_package_entry(
    packages_local: Path,
    package_name: str,
) -> dict[str, str]:
    async with aiofiles.open(packages_local, "rt") as f:
        data = await f.read()
    package_re = re.compile(package_name)
    packages = []
    for stanza in _parse_package_stanzas(data):
        name = stanza.get("Package")
        if name and package_re.fullmatch(name):
            packages.append(stanza)
    if not packages:
        raise LookupError(f"No package {package_name} found")
    packages.sort(
        key=lambda s: tuple(map(int, re.findall(r"\d+", s["Version"])))
    )
    return packages[-1]


class DebIndex(Index):
    def __init__(self, index_id: IndexId, path: Path):
        super().__init__(index_id, path)
        if not isinstance(index_id, DebIndexId):
            raise TypeError(
                f"{type(self).__name__} requires DebIndexId, "
                f"not {type(index_id).__name__}"
            )
        self.index = index_id.repo_url
        self.savedir = path
        self.savedir.mkdir(parents=True, exist_ok=True)
        self.codename = index_id.codename
        self.arch = index_id.arch
        self.category = index_id.category
        self.key = index_id.key

    @classmethod
    def index_id(cls, dc: DistroConfig) -> DebIndexId:
        assert dc.codename is not None
        assert dc.key
        return DebIndexId(
            dc.index,
            dc.codename,
            _deb_arch(dc.arch),
            dc.category or "main",
            dc.key,
        )

    @alru_cache
    async def check(self) -> IndexState:
        metadata = await _query_packages_metadata(
            self.index,
            self.key,
            self.codename,
            self.category,
            self.arch,
        )
        return IndexState(
            self.name, str(self.id), cast(Mapping[str, JSON], metadata)
        )

    @alru_cache
    async def _materialized_packages(self) -> Path:
        state = await self.check()
        data = deb_data(state)
        packages_path = data["packages_path"]
        checksum = data["checksum"]
        url = posixpath.join(self.index, "dists", self.codename, packages_path)
        file = _metadata_cache_path(self.savedir, packages_path, checksum)
        await download_file(url, file, checksum=checksum)
        return await maybe_decompress(file)

    async def resolve(self, dc: DistroConfig) -> Artifact:
        if self.index_id(dc) != self.id:
            raise ValueError(
                f"{type(self).__name__}.resolve() got distro for "
                f"{self.index_id(dc)}, expected {self.id}"
            )
        state = await self.check()
        packages_local = await self._materialized_packages()
        pkg = await _get_package_entry(packages_local, dc.package)
        return Artifact(
            url=posixpath.join(self.index, pkg["Filename"]),
            checksum=("sha256", pkg["SHA256"]),
            signature_url=None,
            source_index_state=state,
            version=pkg.get("Version"),
        )


class DebExtractor(Extractor):
    async def extract_kconfig(
        self, package: Path, output: Path, _: DistroConfig
    ) -> None:
        async with TemporaryDirectory() as td:
            tdpath = Path(td)
            proc = await asyncio.create_subprocess_exec(
                "dpkg-deb",
                "-x",
                package,
                tdpath,
            )
            code = await proc.wait()
            assert code == 0
            candidates = list(tdpath.glob("boot/config*"))
            assert len(candidates) == 1
            shutil.copyfile(candidates[0], output)
