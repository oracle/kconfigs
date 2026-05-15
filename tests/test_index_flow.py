# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
from pathlib import Path
from typing import ClassVar

import pytest

import kconfigs.main as main_module
from kconfigs.extractor import Extractor
from kconfigs.fetcher import DistroConfig
from kconfigs.index import Index
from kconfigs.index import IndexId
from kconfigs.index import IndexRegistry
from kconfigs.index import alru_cache
from kconfigs.model import Artifact
from kconfigs.model import Checksum
from kconfigs.model import IndexState


class RegistryFakeIndex(Index):
    created: ClassVar[list["RegistryFakeIndex"]] = []

    def __init__(self, index_id: IndexId, path: Path):
        super().__init__(index_id, path)
        self.path = path
        self.created.append(self)

    @classmethod
    def index_id(cls, dc: DistroConfig) -> str:
        assert dc.repo is not None
        return f"{dc.index}:{dc.repo}"

    @alru_cache
    async def check(self) -> IndexState:
        return IndexState(self.name, str(self.id), {})

    async def resolve(self, dc: DistroConfig) -> Artifact:
        state = await self.check()
        return Artifact(
            url=f"{dc.index}/package.rpm",
            checksum=None,
            signature_url=None,
            source_index_state=state,
        )


class StaticIndex(Index):
    def __init__(self, state: IndexState, artifact: Artifact):
        super().__init__(state.uid, Path("."))
        self.state = state
        self.artifact = artifact
        self.check_calls = 0
        self.resolve_calls = 0

    @classmethod
    def index_id(cls, dc: DistroConfig) -> str:
        return dc.index

    @alru_cache
    async def check(self) -> IndexState:
        self.check_calls += 1
        return self.state

    async def resolve(self, dc: DistroConfig) -> Artifact:
        self.resolve_calls += 1
        assert await self.check() == self.state
        return self.artifact


class BlockingCheckIndex(Index):
    def __init__(self, state: IndexState):
        super().__init__(state.uid, Path("."))
        self.state = state
        self.check_calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @classmethod
    def index_id(cls, dc: DistroConfig) -> str:
        return dc.index

    @alru_cache
    async def check(self) -> IndexState:
        self.check_calls += 1
        self.started.set()
        await self.release.wait()
        return self.state

    async def resolve(self, dc: DistroConfig) -> Artifact:
        state = await self.check()
        return Artifact(
            url=f"{dc.index}/package.rpm",
            checksum=None,
            signature_url=None,
            source_index_state=state,
        )


class RecordingExtractor(Extractor):
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.extract_calls = 0

    async def extract_kconfig(
        self, package: Path, output: Path, dc: DistroConfig
    ) -> None:
        self.extract_calls += 1
        if self.fail:
            raise RuntimeError("extract failed")
        output.write_text("CONFIG_FAKE=y\n")


class DownloadRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, Checksum | None]] = []

    async def __call__(
        self,
        url: str,
        file: Path,
        always_download: bool = False,
        checksum: Checksum | None = None,
    ) -> None:
        del always_download
        self.calls.append((url, file, checksum))
        file.write_bytes(b"package")


def make_distro(name: str = "Test Distro") -> DistroConfig:
    return DistroConfig(
        name=name,
        arch="x86_64",
        package="kernel",
        fetcher=f"{__name__}.RegistryFakeIndex",
        extractor="unused.extractor",
        index="https://example.com/repo",
        repo="core",
    )


def install_extractor(
    monkeypatch: pytest.MonkeyPatch, extractor: Extractor
) -> None:
    def get_extractor(cls: type[Extractor], kind: str) -> Extractor:
        del cls, kind
        return extractor

    monkeypatch.setattr(Extractor, "get", classmethod(get_extractor))


def test_index_registry_reuses_same_kind_and_index_id(tmp_path: Path) -> None:
    RegistryFakeIndex.created = []
    registry = IndexRegistry(tmp_path)
    distro = make_distro()
    same_index_distro = make_distro(name="Same Index")
    different_index_distro = make_distro(name="Different Index")
    different_index_distro.repo = "extra"

    index = registry.get(distro)

    assert registry.get(same_index_distro) is index
    assert registry.get(different_index_distro) is not index
    assert len(RegistryFakeIndex.created) == 2
    assert RegistryFakeIndex.created[0].path.exists()


def test_alru_cache_coalesces_concurrent_index_checks() -> None:
    async def exercise() -> None:
        state = IndexState("fake.Index", "uid", {"revision": "one"})
        index = BlockingCheckIndex(state)
        check_tasks = [asyncio.create_task(index.check()) for _ in range(3)]

        await index.started.wait()
        await asyncio.sleep(0)

        assert index.check_calls == 1

        index.release.set()
        assert await asyncio.gather(*check_tasks) == [state, state, state]
        assert await index.check() == state
        assert index.check_calls == 1

    asyncio.run(exercise())


def test_failed_index_target_does_not_advance_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    distro = make_distro()
    old_index_state = IndexState("fake.Index", "uid", {"revision": "old"})
    new_index_state = IndexState("fake.Index", "uid", {"revision": "new"})
    old_artifact = Artifact(
        url="https://example.com/kernel.rpm",
        checksum=("sha256", "old-package"),
        signature_url=None,
        source_index_state=old_index_state,
        version="1",
    )
    new_artifact = Artifact(
        url="https://example.com/kernel.rpm",
        checksum=("sha256", "new-package"),
        signature_url=None,
        source_index_state=new_index_state,
        version="2",
    )
    index = StaticIndex(new_index_state, new_artifact)
    extractor = RecordingExtractor(fail=True)
    downloader = DownloadRecorder()
    prior_state = {"artifact": old_artifact.to_json()}

    install_extractor(monkeypatch, extractor)
    monkeypatch.setattr(main_module, "download_file", downloader)
    monkeypatch.setattr(IndexRegistry, "get", lambda self, dc: index)

    new_fetcher_state, new_distro_state, tracker = asyncio.run(
        main_module.run_distro_tasks(
            [distro],
            {},
            {distro.unique_name: prior_state},
            tmp_path / "save",
            tmp_path / "out",
            filtered=False,
            fail_fast=False,
        )
    )

    assert new_fetcher_state == {}
    assert new_distro_state[distro.unique_name] == prior_state
    assert not tracker.success
    assert index.check_calls == 1
    assert index.resolve_calls == 1
    assert extractor.extract_calls == 1
    assert len(downloader.calls) == 1


def test_metadata_only_index_update_advances_state_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    distro = make_distro()
    old_index_state = IndexState("fake.Index", "uid", {"revision": "old"})
    new_index_state = IndexState("fake.Index", "uid", {"revision": "new"})
    old_artifact = Artifact(
        url="https://example.com/kernel.rpm",
        checksum=("sha256", "package"),
        signature_url="https://example.com/kernel.rpm.sig",
        source_index_state=old_index_state,
        version="1",
    )
    new_artifact = Artifact(
        url="https://example.com/kernel.rpm",
        checksum=("sha256", "package"),
        signature_url="https://example.com/kernel.rpm.sig",
        source_index_state=new_index_state,
        version="1",
    )
    index = StaticIndex(new_index_state, new_artifact)
    extractor = RecordingExtractor()
    downloader = DownloadRecorder()
    output = tmp_path / "out" / distro.unique_name / "config"
    output.parent.mkdir(parents=True)
    output.write_text("CONFIG_OLD=y\n")

    install_extractor(monkeypatch, extractor)
    monkeypatch.setattr(main_module, "download_file", downloader)
    monkeypatch.setattr(IndexRegistry, "get", lambda self, dc: index)

    new_fetcher_state, new_distro_state, tracker = asyncio.run(
        main_module.run_distro_tasks(
            [distro],
            {},
            {distro.unique_name: {"artifact": old_artifact.to_json()}},
            tmp_path / "save",
            tmp_path / "out",
            filtered=False,
            fail_fast=False,
        )
    )

    assert new_fetcher_state == {}
    assert new_distro_state[distro.unique_name] == {
        "artifact": new_artifact.to_json()
    }
    assert tracker.success
    assert index.check_calls == 1
    assert index.resolve_calls == 1
    assert extractor.extract_calls == 0
    assert downloader.calls == []
    assert output.read_text() == "CONFIG_OLD=y\n"
