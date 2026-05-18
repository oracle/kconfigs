# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
"""
Interface for indexes, which check metadata and resolve artifacts.
"""

import abc
import asyncio
import importlib
from collections.abc import Callable
from collections.abc import Coroutine
from collections.abc import Hashable
from functools import cache
from functools import wraps
from pathlib import Path
from typing import Any
from typing import TypeAlias
from typing import TypeVar
from typing import cast

from kconfigs.distro import DistroConfig
from kconfigs.model import Artifact
from kconfigs.model import IndexState

IndexId: TypeAlias = Hashable
R = TypeVar("R")


@cache
def load_implementation(kind: str) -> type[Any]:
    modname, klassname = kind.rsplit(".", maxsplit=1)
    mod = importlib.import_module(modname)
    klass = getattr(mod, klassname)
    if not isinstance(klass, type):
        raise TypeError(f"{kind} is not a class")
    return klass


def alru_cache(
    func: Callable[..., Coroutine[Any, Any, R]],
) -> Callable[..., Coroutine[Any, Any, R]]:
    """
    Cache async function calls by argument, sharing one task among callers.
    """
    tasks: dict[tuple[object, ...], asyncio.Task[R]] = {}

    @wraps(func)
    async def wrapper(*args: object) -> R:
        key = args
        task = tasks.get(key)
        if task is None:
            task = asyncio.create_task(func(*args))
            tasks[key] = task
        return await asyncio.shield(task)

    return cast(Callable[..., Coroutine[Any, Any, R]], wrapper)


class Index(abc.ABC):
    """
    Shared source of package metadata.

    One Index may serve multiple distros when those targets share the same Index
    kind and ID. For instance, a single Yum repo could provide multiple kernel
    packages (kernel-a, kernel-b). Both packages could be independent targets
    and share the same Index.
    """

    name: str
    id: IndexId
    path: Path

    def __init__(self, index_id: IndexId, path: Path):
        """
        Initialize the index ID and persistent cache directory.
        """
        self.name = getattr(
            type(self), "name", f"{type(self).__module__}.{type(self).__name__}"
        )
        self.id = index_id
        self.path = path

    @classmethod
    @abc.abstractmethod
    def index_id(cls, dc: DistroConfig) -> IndexId:
        """
        Return the unique identifier of the index, which determines sharing
        """

    @abc.abstractmethod
    async def check(self) -> IndexState:
        """
        Check lightweight metadata and return the current index state.
        """

    @abc.abstractmethod
    async def resolve(self, dc: DistroConfig) -> Artifact:
        """
        Resolve an artifact for one distro target from the current checked state.
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
        self.registry: dict[tuple[str, IndexId], Index] = {}
        self.workdir = workdir

    def get(self, dc: DistroConfig) -> Index:
        index_cls = Index.get(dc.fetcher)
        index_id = index_cls.index_id(dc)
        if (dc.fetcher, index_id) not in self.registry:
            trans = str.maketrans(":/?", "___")
            index_dir = (
                self.workdir
                / "index"
                / dc.fetcher
                / str(index_id).translate(trans)
            )
            index_dir.mkdir(exist_ok=True, parents=True)
            self.registry[(dc.fetcher, index_id)] = index_cls(
                index_id, index_dir
            )
        return self.registry[(dc.fetcher, index_id)]
