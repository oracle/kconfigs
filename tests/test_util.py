# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import asyncio
import hashlib
from pathlib import Path
from types import TracebackType
from typing import Any
from typing import AsyncIterator
from typing import cast

import pytest

from kconfigs.util import DownloadManager


class FakeContent:
    def __init__(self, data: bytes):
        self.data = data

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        del size
        await asyncio.sleep(0)
        yield self.data


class FakeResponse:
    def __init__(self, data: bytes):
        self.content = FakeContent(data)


class FakeGet:
    def __init__(self, data: bytes):
        self.response = FakeResponse(data)

    async def __aenter__(self) -> FakeResponse:
        return self.response

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


class FakeSession:
    def __init__(self, payloads: list[bytes]):
        self.payloads = payloads
        self.urls: list[str] = []

    def get(self, url: str) -> FakeGet:
        self.urls.append(url)
        return FakeGet(self.payloads.pop(0))


def make_download_manager(payloads: list[bytes]) -> DownloadManager:
    manager = object.__new__(DownloadManager)
    manager.session = cast(Any, FakeSession(payloads))
    manager.sem = asyncio.Semaphore(1)
    return manager


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_download_file_does_not_publish_failed_checksum(
    tmp_path: Path,
) -> None:
    manager = make_download_manager([b"bad"])
    target = tmp_path / "metadata.db"

    with pytest.raises(Exception, match="Failed to verify sha256 checksum"):
        asyncio.run(
            manager.download_file(
                "https://example.com/metadata.db",
                target,
                checksum=("sha256", sha256(b"good")),
            )
        )

    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_download_file_replaces_bad_cached_file(tmp_path: Path) -> None:
    manager = make_download_manager([b"good"])
    target = tmp_path / "metadata.db"
    target.write_bytes(b"bad")

    asyncio.run(
        manager.download_file(
            "https://example.com/metadata.db",
            target,
            checksum=("sha256", sha256(b"good")),
        )
    )

    assert target.read_bytes() == b"good"
