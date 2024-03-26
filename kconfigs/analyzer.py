# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import argparse
import json
from configparser import ConfigParser
from pathlib import Path
from typing import TextIO

from kconfigs.fetcher import DistroConfig


def parse_kconfig(filp: TextIO) -> dict[str, str | None]:
    config: dict[str, str | None] = {}
    for line in filp.readlines():
        line = line.strip()
        if not line:
            continue
        elif line.startswith("# Linux/") or not line:
            uname = line.split()[2]
            config["UTS_RELEASE"] = uname
        elif line.startswith("# CONFIG_"):
            key = line.split()[1]
            config[key] = None
        elif line.startswith("#"):
            continue
        else:
            key, value = line.split("=", 1)
            assert key not in config
            config[key] = value
    assert "UTS_RELEASE" in config
    return config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Distribution kconfig combiner and analyzer"
    )
    parser.add_argument(
        "config",
        help="configuration file",
        type=Path,
    )
    parser.add_argument(
        "--input-dir",
        help="directory containing configs (--output-dir from downloader)",
        type=Path,
        default=Path.cwd() / "out",
    )
    parser.add_argument(
        "--output-file",
        help="output JSON file for summarized configs",
        type=Path,
        default=Path.cwd() / "out/summary.json",
    )

    args = parser.parse_args()

    cfg = ConfigParser()
    cfg.read(args.config)
    distros = [DistroConfig(**dict(cfg[sec])) for sec in cfg.sections()]

    kconfigs = {}
    kconfig_keys: set[str] = set()
    for distro in distros:
        config_file = args.input_dir / distro.unique_name / "config"
        with config_file.open("rt") as f:
            kconfig = parse_kconfig(f)
        kconfigs[distro.unique_name] = kconfig
        kconfig_keys.update(kconfig)

    print("not set\tyes\tmod\tother\tdistro")
    kconfig_to_distro_list: dict[str, list[str | None]] = {
        c.removeprefix("CONFIG_"): list() for c in kconfig_keys
    }
    distro_list = []
    for distro in distros:
        distro_list.append(
            {
                "unique_name": distro.unique_name,
                "name": distro.name,
                "version": distro.version,
                "arch": distro.arch,
                "package": distro.package,
            }
        )

        count_missing = 0
        count_yes = 0
        count_mod = 0
        count_other = 0

        kconfig = kconfigs[distro.unique_name]
        for key in kconfig_keys:
            val = kconfig.get(key)
            kconfig_to_distro_list[key.removeprefix("CONFIG_")].append(val)
            if val is None:
                count_missing += 1
            elif val == "y":
                count_yes += 1
            elif val == "m":
                count_mod += 1
            else:
                count_other += 1
        print(
            f"{count_missing}\t{count_yes}\t{count_mod}\t{count_other}\t{distro.unique_name}"
        )

    with args.output_file.open("wt") as f:
        obj = {
            "distros": distro_list,
            "kconfigs": kconfig_to_distro_list,
        }
        json.dump(obj, f)


if __name__ == "__main__":
    main()
