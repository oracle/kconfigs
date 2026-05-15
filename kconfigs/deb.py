# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import posixpath
import re
import shutil
from pathlib import Path
from typing import Any
from typing import Mapping
from typing import NamedTuple
from typing import TypedDict
from typing import cast

import aiofiles
from aiofiles.tempfile import TemporaryDirectory

from kconfigs.extractor import Extractor
from kconfigs.fetcher import Checksum
from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
from kconfigs.index import Index
from kconfigs.index import IndexId
from kconfigs.index import alru_cache
from kconfigs.model import JSON
from kconfigs.model import Artifact
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


async def _get_relevant_keys(
    packages_local: Path, flavor: str
) -> dict[str, dict[str, str]]:
    async with aiofiles.open(packages_local, "rt") as f:
        data = await f.read()
    keys: dict[str, dict[str, str]] = {}
    pkg_re = re.compile(rf"linux-.*{re.escape(flavor)}")
    for stanza in _parse_package_stanzas(data):
        name = stanza.get("Package")
        if name and pkg_re.fullmatch(name):
            keys[name] = stanza
    return keys


def _dependency_name(dep: str) -> str:
    dep = dep.split("|", maxsplit=1)[0].strip()
    if " " in dep:
        dep, _ = dep.split(maxsplit=1)
    return dep


def _resolve_concrete_package(
    keys: dict[str, dict[str, str]], flavor: str
) -> str:
    deps = (
        keys[f"linux-image-{flavor}"]["Depends"].replace("\n", " ").split(",")
    )
    for dep in deps:
        pkg = _dependency_name(dep)
        if pkg.startswith("linux-image"):
            # Debian Forky now has linux-base instead.
            base_pkg = pkg.replace("linux-image", "linux-base")
            if base_pkg in keys:
                return base_pkg
            modules_pkg = pkg.replace("linux-image", "linux-modules")
            if modules_pkg in keys:
                return modules_pkg
            if pkg in keys:
                return pkg
            raise Exception(f"Could not find concrete package: {pkg}")
    raise Exception("Could not find specific linux-modules package")


class DebFetcher(Fetcher):
    def __init__(
        self, saved_data: dict[str, Any], dc: DistroConfig, savedir: Path
    ):
        self.index = dc.index
        self.savedir = savedir
        self.__last_hash: None | str = saved_data.get("last_hash")
        self.__latest_hash: None | str = None
        self.__packages_path: None | str = None
        self.__packages_local: None | Path = None
        self.__arch = RPM_TO_DEB_ARCH.get(dc.arch, dc.arch)
        self.__category = dc.category or "main"
        assert dc.codename is not None
        self.__codename = dc.codename
        assert dc.key
        self.key = dc.key

    @classmethod
    def uid(cls, dc: DistroConfig) -> str:
        arch = RPM_TO_DEB_ARCH.get(dc.arch, dc.arch)
        return "-".join([dc.index, str(dc.codename), arch, str(dc.category)])

    def save_data(self) -> dict[str, Any]:
        return {"last_hash": self.__latest_hash or self.__last_hash}

    async def __query_latest_hash(self) -> None:
        metadata = await _query_packages_metadata(
            self.index,
            self.key,
            self.__codename,
            self.__category,
            self.__arch,
        )
        self.__latest_hash = metadata["checksum"][1]
        self.__packages_path = metadata["packages_path"]

    async def is_updated(self) -> bool:
        if not self.__latest_hash:
            await self.__query_latest_hash()
        return self.__latest_hash != self.__last_hash

    async def __fetch_latest_packages(self) -> None:
        if not self.__latest_hash:
            await self.__query_latest_hash()
        assert self.__latest_hash
        assert self.__packages_path
        url = posixpath.join(
            self.index, "dists", self.__codename, self.__packages_path
        )
        name = posixpath.basename(url)
        file = self.savedir / name
        await download_file(
            url,
            file,
            always_download=await self.is_updated(),
            checksum=("sha256", self.__latest_hash),
        )
        self.__packages_local = await maybe_decompress(file)

    async def __get_relevant_keys(
        self, flavor: str
    ) -> dict[str, dict[str, str]]:
        assert self.__packages_local
        return await _get_relevant_keys(self.__packages_local, flavor)

    async def latest_version_url(self, pkg: str) -> tuple[str, Checksum | None]:
        if not self.__packages_local:
            await self.__fetch_latest_packages()
        m = re.fullmatch(r"linux-(.*)", pkg)
        assert m
        flavor = m.group(1)
        keys = await self.__get_relevant_keys(flavor)

        # For Ubuntu at least, the packages are wildly messed up.
        # Assume for a moment we're looking at flavor=generic.
        # We have "linux-generic" depending on "linux-image-generic", which
        # depends on "linux-image-$UNAME-generic" which depends on
        # "linux-modules-$UNAME-generic". Whew. All *we* want is the config,
        # which seems to be contained in linux-modules-$UNAME-generic. The
        # quickest route to this is to find "linux-image-$FLAVOR", get the
        # specific package name dependency ("linux-image-$UNAME-$FLAVOR"),
        # and then replace that with linux-modules.
        pkg = _resolve_concrete_package(keys, flavor)
        url = posixpath.join(self.index, keys[pkg]["Filename"])
        checksum = ("sha256", keys[pkg]["SHA256"])
        return (url, checksum)


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
        m = re.fullmatch(r"linux-(.*)", dc.package)
        assert m
        flavor = m.group(1)
        keys = await _get_relevant_keys(packages_local, flavor)
        pkg = _resolve_concrete_package(keys, flavor)
        return Artifact(
            url=posixpath.join(self.index, keys[pkg]["Filename"]),
            checksum=("sha256", keys[pkg]["SHA256"]),
            signature_url=None,
            source_index_state=state,
            version=keys[pkg].get("Version"),
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
