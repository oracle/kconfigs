# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import argparse
import asyncio
import configparser
import json
import multiprocessing
import posixpath
import shutil
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from kconfigs.extractor import Extractor
from kconfigs.fetcher import DistroConfig
from kconfigs.fetcher import Fetcher
from kconfigs.util import download_file
from kconfigs.util import download_manager


# Extraction is CPU-bound, and it also consumes quite a bit of disk space.
# Limit the number of CPUs which can do extraction in parallel.
extract_sem = asyncio.Semaphore(multiprocessing.cpu_count() + 1)


class FetcherFactory:
    def __init__(self, state: dict[str, Any], workdir: Path):
        self.registry: dict[tuple[str, str], Fetcher] = {}
        self.state = state
        self.workdir = workdir

    def get(self, dc: DistroConfig) -> Fetcher:
        fetcher_cls = Fetcher.get(dc.fetcher)
        uid = fetcher_cls.uid(dc)
        if (dc.fetcher, uid) not in self.registry:
            trans = str.maketrans(":/?", "___")
            fetcher_state = self.state.get(dc.fetcher, {}).get(uid, {})
            fetcher_dir = (
                self.workdir / "fetcher" / dc.fetcher / uid.translate(trans)
            )
            fetcher_dir.mkdir(exist_ok=True, parents=True)
            self.registry[(dc.fetcher, uid)] = fetcher_cls(
                fetcher_state, dc, fetcher_dir
            )
        return self.registry[(dc.fetcher, uid)]

    def save_state(self) -> dict[str, dict[str, Any]]:
        new_state: dict[str, dict[str, dict[str, Any]]] = {}
        for (kind, uid), fetcher in self.registry.items():
            new_state.setdefault(kind, {})[uid] = fetcher.save_data()
        return new_state


async def run_for_distro(
    d: DistroConfig,
    fetcher: Fetcher,
    state: dict[str, Any],
    save_dir: Path,
    out_dir: Path,
) -> tuple[DistroConfig, dict[str, Any]]:
    workdir = save_dir / "distro" / d.unique_name

    out = out_dir / d.unique_name / "config"
    out.parent.mkdir(exist_ok=True, parents=True)

    previous_url = state.get("latest_url", "NONE")
    if d.do_update and await fetcher.is_updated():
        if workdir.exists():
            shutil.rmtree(workdir)
        workdir.mkdir(parents=True)
        latest_url, maybe_csum = await fetcher.latest_version_url(d.package)
        if latest_url != previous_url:
            async with extract_sem:
                name = posixpath.basename(latest_url)
                file = workdir / name
                await download_file(latest_url, file, checksum=maybe_csum)

                extractor = Extractor.get(d.extractor)

                maybe_sig = await fetcher.signature_url(d.package)
                if maybe_sig:
                    signame = posixpath.basename(maybe_sig)
                    sigfile = workdir / signame
                    await download_file(maybe_sig, sigfile)
                    await extractor.verify_signature(file, sigfile, d)

                print(f"Extract config of {d.unique_name}")
                await extractor.extract_kconfig(file, out, d)
    else:
        latest_url = previous_url
    if workdir.exists():
        # Clear the distro's work directory to conserve space
        shutil.rmtree(workdir)
    return d, {"latest_url": latest_url}


def get_distros(
    cfg: configparser.ConfigParser, f: list[str]
) -> list[DistroConfig]:
    distros = []
    for sec in cfg.sections():
        if f and not any(fnmatch(sec, pat) for pat in f):
            continue
        args: dict[str, Any] = dict(cfg[sec])
        # handle non-string configs
        if "do_update" in args:
            args["do_update"] = cfg[sec].getboolean("do_update")
        distros.append(DistroConfig(**args))
    return distros


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
        default=Path("state.json"),
        type=Path,
        help="JSON file which will hold state of last download",
    )
    parser.add_argument(
        "--download-dir",
        default=Path.cwd() / "save",
        type=Path,
        help="directory where downloads will get stored",
    )
    parser.add_argument(
        "--output-dir",
        default=Path.cwd() / "out",
        type=Path,
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

    args = parser.parse_args()
    cfg = configparser.ConfigParser()
    cfg.read(args.config)

    if args.state.exists():
        with args.state.open() as f:
            state = json.load(f)
    else:
        state = {}

    fetcher_state = state.get("fetchers", {})
    distro_state = state.get("distros", {})

    distros = get_distros(cfg, args.filter)
    fetchers = FetcherFactory(fetcher_state, args.download_dir)

    if args.filter:
        new_fetcher_state = fetcher_state.copy()
        new_distro_state = distro_state.copy()
    else:
        new_fetcher_state = {}
        new_distro_state = {}

    async with asyncio.TaskGroup() as tg:
        tasks = []
        for distro in distros:
            fetcher = fetchers.get(distro)
            state = distro_state.get(distro.unique_name, {})
            fut = tg.create_task(
                run_for_distro(
                    distro, fetcher, state, args.download_dir, args.output_dir
                )
            )
            fut.set_name(distro.unique_name)
            tasks.append(fut)

        for fut in asyncio.as_completed(tasks):  # type: ignore
            distro, state = await fut
            new_distro_state[distro.unique_name] = state

    new_fetcher_state.update(fetchers.save_state())

    with args.state.open("wt") as f:
        data = {
            "fetchers": new_fetcher_state,
            "distros": new_distro_state,
        }
        json.dump(data, f, sort_keys=True, indent=4)
        f.write("\n")  # newline at end of file for the git hooks

    await download_manager().session.close()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
