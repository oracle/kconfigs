# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
"""
Interface for indexes, which synchronize metadata and resolve artifacts.
"""

import abc
import asyncio
import importlib
from functools import cache
from pathlib import Path
from typing import Any

from kconfigs.fetcher import DistroConfig
from kconfigs.model import Artifact
from kconfigs.model import IndexState


@cache
def load_implementation(kind: str) -> type[Any]:
    modname, klassname = kind.rsplit(".", maxsplit=1)
    mod = importlib.import_module(modname)
    klass = getattr(mod, klassname)
    if not isinstance(klass, type):
        raise TypeError(f"{kind} is not a class")
    return klass


class Index(abc.ABC):
    """
    Shared source of package metadata.

    One Index may serve multiple distro targets when those targets share the
    same implementation and UID.
    """

    name: str
    _sync_lock: asyncio.Lock
    _sync_task: asyncio.Task[IndexState] | None

    def __init__(self, dc: DistroConfig, savedir: Path):
        """
        Initialize the index with configuration and a persistent cache directory.
        """
        self._sync_lock = asyncio.Lock()
        self._sync_task = None

    @classmethod
    @abc.abstractmethod
    def uid(cls, dc: DistroConfig) -> str:
        """
        Return the sharing boundary for this index implementation.
        """

    async def sync(self) -> IndexState:
        """
        Synchronize lightweight metadata and return the current index state.
        """
        async with self._sync_lock:
            if self._sync_task is None:
                self._sync_task = asyncio.create_task(self._sync())
            task = self._sync_task
        return await task

    @abc.abstractmethod
    async def _sync(self) -> IndexState:
        """
        Implementation hook for sync().

        The public sync() method coalesces concurrent callers and calls this
        hook exactly once per Index object.
        """

    @abc.abstractmethod
    async def resolve(self, state: IndexState, dc: DistroConfig) -> Artifact:
        """
        Resolve an artifact for one distro target from a synchronized state.
        """

    @classmethod
    @cache
    def get(cls, kind: str) -> type["Index"]:
        klass = load_implementation(kind)
        if not issubclass(klass, cls):
            raise TypeError(f"{kind} is not an Index")
        klass.name = kind
        return klass


class IndexRegistry:
    def __init__(self, workdir: Path):
        self.registry: dict[tuple[str, str], Index] = {}
        self.workdir = workdir

    def get(self, dc: DistroConfig) -> Index:
        index_cls = Index.get(dc.fetcher)
        uid = index_cls.uid(dc)
        if (dc.fetcher, uid) not in self.registry:
            trans = str.maketrans(":/?", "___")
            index_dir = (
                self.workdir / "index" / dc.fetcher / uid.translate(trans)
            )
            index_dir.mkdir(exist_ok=True, parents=True)
            self.registry[(dc.fetcher, uid)] = index_cls(dc, index_dir)
        return self.registry[(dc.fetcher, uid)]
