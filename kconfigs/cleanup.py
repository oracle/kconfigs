# Copyright (c) 2024, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import argparse
import shutil
from configparser import ConfigParser
from pathlib import Path

from kconfigs.main import get_distros


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
        "--filter",
        "-f",
        action="append",
        default=[],
        help="Filter to only the given config.ini sections (fnmatch(3) patterns"
        "are accepted)",
    )

    args = parser.parse_args()

    cfg = ConfigParser()
    cfg.read(args.config)
    distros = get_distros(cfg, args.filter)
    names = set(d.unique_name for d in distros)
    for path in args.input_dir.iterdir():
        if path.name not in names:
            print(f'Removing "{path.name}"')
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


if __name__ == "__main__":
    main()
