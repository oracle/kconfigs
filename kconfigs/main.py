# Copyright (c) 2024, 2026 Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import argparse
import asyncio
import configparser
import json
import multiprocessing
import posixpath
import shutil
import sys
import traceback
from collections.abc import AsyncIterator
from collections.abc import Coroutine
from collections.abc import Mapping
from fnmatch import fnmatch
from pathlib import Path
from string import Template
from types import TracebackType
from typing import Any
from typing import Self
from typing import cast

from kconfigs.distro import DistroConfig
from kconfigs.extractor import Extractor
from kconfigs.index import Index
from kconfigs.index import IndexKey
from kconfigs.index import IndexRegistry
from kconfigs.model import Artifact
from kconfigs.util import download_file
from kconfigs.util import download_manager

# Extraction is CPU-bound, and it also consumes quite a bit of disk space.
# Limit the number of CPUs which can do extraction in parallel.
extract_sem = asyncio.Semaphore(multiprocessing.cpu_count() + 1)

TEMPLATE_VARIABLES = frozenset(
    ("version", "arch", "codename", "package", "uekver")
)
SUBSTITUTED_TEMPLATE_FIELDS = frozenset(("name", "index", "key"))


def artifact_from_state(state: dict[str, Any]) -> Artifact | None:
    artifact_data = state.get("artifact")
    if not isinstance(artifact_data, Mapping):
        return None
    return Artifact.from_json(cast(Mapping[str, object], artifact_data))


def legacy_latest_url_from_state(state: dict[str, Any]) -> str | None:
    latest_url = state.get("latest_url")
    if isinstance(latest_url, str) and latest_url != "NONE":
        return latest_url
    return None


def success_state(artifact: Artifact) -> dict[str, Any]:
    return {"artifact": artifact.to_json()}


async def download_and_extract_artifact(
    d: DistroConfig,
    artifact: Artifact,
    workdir: Path,
    out: Path,
) -> None:
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    try:
        async with extract_sem:
            name = posixpath.basename(artifact.url)
            file = workdir / name
            await download_file(artifact.url, file, checksum=artifact.checksum)

            extractor = Extractor.get(d.extractor)

            if artifact.signature_url:
                signame = posixpath.basename(artifact.signature_url)
                sigfile = workdir / signame
                await download_file(artifact.signature_url, sigfile)
                await extractor.verify_signature(file, sigfile, d)

            print(f"Extract config of {d.name}")
            await extractor.extract_kconfig(file, out, d)
    finally:
        if workdir.exists():
            shutil.rmtree(workdir)


async def run_for_index_distro(
    d: DistroConfig,
    index: Index,
    state: dict[str, Any],
    save_dir: Path,
    out_dir: Path,
) -> tuple[DistroConfig, dict[str, Any]]:
    workdir = save_dir / "distro" / d.name

    out = out_dir / d.name / "config"
    out.parent.mkdir(exist_ok=True, parents=True)

    old_artifact = artifact_from_state(state)
    legacy_latest_url = legacy_latest_url_from_state(state)

    if not d.do_update:
        if old_artifact and not out.exists():
            await download_and_extract_artifact(d, old_artifact, workdir, out)
            return d, success_state(old_artifact)
        return d, state

    index_state = await index.check()

    if old_artifact and old_artifact.source_index_state == index_state:
        if out.exists():
            return d, success_state(old_artifact)
        await download_and_extract_artifact(d, old_artifact, workdir, out)
        return d, success_state(old_artifact)

    artifact = await index.resolve(d)
    if artifact.source_index_state != index_state:
        raise ValueError(
            f"{type(index).__name__}.resolve() returned an artifact for a "
            "different index state"
        )
    if (
        old_artifact
        and old_artifact.same_download_as(artifact)
        and out.exists()
    ):
        return d, success_state(artifact)
    if (
        old_artifact is None
        and legacy_latest_url == artifact.url
        and out.exists()
    ):
        return d, success_state(artifact)

    await download_and_extract_artifact(d, artifact, workdir, out)
    return d, success_state(artifact)


def get_distros(
    cfg: configparser.ConfigParser, f: list[str]
) -> list[DistroConfig]:
    templates = {
        sec[:-1]: sec
        for sec in cfg.sections()
        if sec.endswith(":") and sec[:-1]
    }
    distros = []
    for sec in cfg.sections():
        if sec.endswith(":"):
            if not sec[:-1]:
                raise ValueError("template section name must not be empty")
            continue

        section_name = _distro_section_name(sec)
        args = _expanded_section_args(cfg, sec, templates)
        # handle non-string configs
        if "do_update" in args:
            args["do_update"] = _convert_config_bool(
                args["do_update"], sec, "do_update"
            )
        distros.append((sec, section_name, DistroConfig(**args)))
    _check_unique_distro_names(distros)
    return [
        distro
        for sec, section_name, distro in distros
        if not f
        or any(fnmatch(section_name, pat) or fnmatch(sec, pat) for pat in f)
    ]


def _check_unique_distro_names(
    distros: list[tuple[str, str, DistroConfig]],
) -> None:
    sections_by_name: dict[str, str] = {}
    for section, _, distro in distros:
        if distro.name in sections_by_name:
            raise ValueError(
                f"sections [{sections_by_name[distro.name]}] and "
                f"[{section}] resolve to duplicate distro name {distro.name!r}"
            )
        sections_by_name[distro.name] = section


def _section_template(section: str) -> tuple[str, str] | None:
    template, separator, section_name = section.partition(":")
    if not separator:
        return None
    if not template:
        raise ValueError(f"section [{section}] has an empty template name")
    if not section_name:
        return None
    return template, section_name


def _distro_section_name(section: str) -> str:
    template = _section_template(section)
    if template is None:
        return section
    _, section_name = template
    return section_name


def _expanded_section_args(
    cfg: configparser.ConfigParser,
    section: str,
    templates: Mapping[str, str],
) -> dict[str, Any]:
    args: dict[str, Any] = {}
    section_template = _section_template(section)
    if section_template is not None:
        template, _ = section_template
        try:
            template_section = templates[template]
        except KeyError as e:
            raise ValueError(
                f"section [{section}] uses unknown template [{template}:]"
            ) from e
        args.update(cfg[template_section])
    args.update(cfg[section])
    _substitute_template_variables(args, section)
    return args


def _substitute_template_variables(args: dict[str, Any], section: str) -> None:
    variables = {
        key: args[key]
        for key in TEMPLATE_VARIABLES
        if key in args and isinstance(args[key], str)
    }
    for field in SUBSTITUTED_TEMPLATE_FIELDS:
        value = args.get(field)
        if not isinstance(value, str):
            continue
        try:
            args[field] = Template(value).substitute(variables)
        except KeyError as e:
            missing = cast(str, e.args[0])
            raise ValueError(
                f"section [{section}] field {field} references "
                f"unset variable ${missing}"
            ) from e
        except ValueError as e:
            raise ValueError(
                f"section [{section}] field {field} has invalid "
                f"template syntax: {e}"
            ) from e


def _convert_config_bool(value: object, section: str, key: str) -> bool:
    if not isinstance(value, str):
        raise TypeError(f"section [{section}] field {key} must be a string")
    try:
        return configparser.ConfigParser.BOOLEAN_STATES[value.lower()]
    except KeyError as e:
        raise ValueError(
            f"section [{section}] field {key} is not a boolean: {value!r}"
        ) from e


def AbsPath(s: str) -> Path:
    return Path(s).absolute()


def index_target_groups(
    distros: list[DistroConfig],
) -> dict[IndexKey, set[str]]:
    groups: dict[IndexKey, set[str]] = {}
    for distro in distros:
        key = IndexRegistry.index_key(distro)
        groups.setdefault(key, set()).add(distro.name)
    return groups


def cleanup_completed_indexes(
    indexes: IndexRegistry,
    configured_index_targets: dict[IndexKey, set[str]],
    selected_index_targets: dict[IndexKey, set[str]],
    succeeded_index_targets: dict[IndexKey, set[str]],
) -> None:
    for key, selected_targets in selected_index_targets.items():
        configured_targets = configured_index_targets[key]
        succeeded_targets = succeeded_index_targets.get(key, set())
        if (
            selected_targets == configured_targets
            and succeeded_targets == configured_targets
            and key in indexes.registry
        ):
            indexes.registry[key].cleanup()


class TaskTracker:
    """
    A simple tracker for asyncio.Task objects that cancels on exit.

    Where TaskGroup cancels tasks on the first failure, TaskTracker is less
    opinionated. It just maintains the lists of tasks, and when you exit its
    context, it cancels any pending ones and waits for everything to complete.
    This means you can implement either fail-fast logic, or logic to fail once
    all are complete.
    """

    def __init__(self) -> None:
        self.pending: set[asyncio.Task[Any]] = set()
        self.failed: set[asyncio.Task[Any]] = set()
        self.cancelled: set[asyncio.Task[Any]] = set()
        self.succeeded: set[asyncio.Task[Any]] = set()
        self.in_context = False
        self.done = False
        self.success = True

    async def __aenter__(self) -> Self:
        self.in_context = True
        return self

    def _account_completed(self, task: asyncio.Task[Any]) -> bool:
        self.pending.remove(task)
        if task.cancelled():
            self.success = False
            self.cancelled.add(task)
            return False
        elif task.exception():
            self.success = False
            self.failed.add(task)
            return False
        else:
            self.succeeded.add(task)
            return True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Cancel any task not yet complete
        for task in self.pending:
            if not task.done():
                task.cancel()
        # Wait for all tasks.
        if self.pending:
            done, _ = await asyncio.wait(self.pending)
            for task in done:
                self._account_completed(task)
        assert not self.pending
        self.done = True

    def create_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        if not self.in_context:
            raise RuntimeError("You must enter the context before adding tasks")
        task = asyncio.create_task(coro)
        self.pending.add(task)
        return task

    async def as_completed(
        self,
    ) -> AsyncIterator[tuple[bool, asyncio.Task[Any]]]:
        while self.pending:
            done, _ = await asyncio.wait(
                self.pending, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                success = self._account_completed(task)
                yield success, task
            if not self.pending:
                self.done = True

    def _report_names(self, ts: set[asyncio.Task[Any]], kind: str) -> None:
        cns = ", ".join(t.get_name() for t in ts)
        cns = f" ({cns})" if cns else cns
        print(f"{len(ts)} task(s) {kind}")

    def report(self) -> None:
        if not self.done:
            raise RuntimeError("Report can only be printed on completion")
        if self.failed or self.cancelled:
            for task in self.failed:
                print(f"TASK FAILED: {task.get_name()}")
                exc = task.exception()
                assert exc is not None
                traceback.print_exception(exc)
                print("-" * 60)
            print(f"{len(self.failed)} task(s) failed")
            self._report_names(self.cancelled, "cancelled")
            self._report_names(self.succeeded, "succeeded")
            print("FAILURE")
        else:
            print("All tasks succeeded!")


async def run_distro_tasks(
    distros: list[DistroConfig],
    distro_state: dict[str, Any],
    download_dir: Path,
    output_dir: Path,
    *,
    filtered: bool,
    fail_fast: bool,
    all_distros: list[DistroConfig] | None = None,
) -> tuple[dict[str, Any], TaskTracker]:
    if all_distros is None:
        all_distros = distros
    indexes = IndexRegistry(download_dir)
    configured_index_targets = index_target_groups(all_distros)
    selected_index_targets = index_target_groups(distros)
    succeeded_index_targets: dict[IndexKey, set[str]] = {}
    task_index_keys: dict[asyncio.Task[Any], IndexKey] = {}

    if filtered:
        new_distro_state = distro_state.copy()
    else:
        selected_distros = {d.name for d in distros}
        new_distro_state = {
            name: state
            for name, state in distro_state.items()
            if name in selected_distros
        }

    async with TaskTracker() as tg:
        for distro in distros:
            state = distro_state.get(distro.name, {})
            key = IndexRegistry.index_key(distro)
            index = indexes.get(distro)
            task = tg.create_task(
                run_for_index_distro(
                    distro,
                    index,
                    state,
                    download_dir,
                    output_dir,
                )
            )
            task.set_name(distro.name)
            task_index_keys[task] = key

        async for success, task in tg.as_completed():
            if not success:
                if fail_fast:
                    break
                else:
                    continue
            distro, state = await task
            new_distro_state[distro.name] = state
            key = task_index_keys[task]
            succeeded_index_targets.setdefault(key, set()).add(distro.name)

    cleanup_completed_indexes(
        indexes,
        configured_index_targets,
        selected_index_targets,
        succeeded_index_targets,
    )
    return new_distro_state, tg


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="downloads and catalogs kernel configs"
    )
    parser.add_argument(
        "config",
        help="configuration file",
    )
    parser.add_argument(
        "--state",
        default=AbsPath("state.json"),
        type=AbsPath,
        help="JSON file which will hold state of last download",
    )
    parser.add_argument(
        "--download-dir",
        default=Path.cwd() / "save",
        type=AbsPath,
        help="directory where downloads will get stored",
    )
    parser.add_argument(
        "--output-dir",
        default=Path.cwd() / "out",
        type=AbsPath,
        help="directory where configs are stored",
    )
    parser.add_argument(
        "--filter",
        "-f",
        action="append",
        default=[],
        help="Filter to only the given config.ini sections (fnmatch(3) patterns"
        "are accepted)",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Fail immediately on error, canceling other work",
    )

    args = parser.parse_args()
    cfg = configparser.ConfigParser()
    cfg.read(args.config)

    if args.state.exists():
        with args.state.open() as f:
            state = json.load(f)
    else:
        state = {}

    distro_state = state.get("distros", {})

    all_distros = get_distros(cfg, [])
    distros = get_distros(cfg, args.filter)
    new_distro_state, tg = await run_distro_tasks(
        distros,
        distro_state,
        args.download_dir,
        args.output_dir,
        filtered=bool(args.filter),
        fail_fast=args.fail_fast,
        all_distros=all_distros,
    )

    with args.state.open("wt") as f:
        data = {
            "distros": new_distro_state,
        }
        json.dump(data, f, sort_keys=True, indent=4)
        f.write("\n")  # newline at end of file for the git hooks

    await download_manager().session.close()
    tg.report()
    if not tg.success:
        sys.exit(1)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
