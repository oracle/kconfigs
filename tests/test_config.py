# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import configparser
from pathlib import Path

import pytest

from kconfigs.main import get_distros


def parse_config(text: str) -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    cfg.read_string(text)
    return cfg


def test_config_ini_expands_with_unique_distro_names() -> None:
    cfg = configparser.ConfigParser()
    cfg.read(Path(__file__).parents[1] / "config.ini")

    distros = get_distros(cfg, [])
    names = {distro.name for distro in distros}

    assert distros
    assert len(names) == len(distros)


def test_get_distros_expands_templates_and_variables() -> None:
    cfg = parse_config(
        """
[rpm:]
name = Test Linux $version $arch
package = kernel-core
fetcher = kconfigs.rpm.RpmIndex
extractor = kconfigs.rpm.RpmExtractor
index = https://repo.example.com/$version/$arch/$package/
key = RPM-GPG-KEY-$version-$package

[rpm:test_1_x86_64]
version = 1
arch = x86_64
package = kernel-lts
"""
    )

    distros = get_distros(cfg, [])

    assert len(distros) == 1
    assert distros[0].name == "Test Linux 1 x86_64"
    assert distros[0].version == "1"
    assert distros[0].arch == "x86_64"
    assert distros[0].package == "kernel-lts"
    assert distros[0].fetcher == "kconfigs.rpm.RpmIndex"
    assert distros[0].extractor == "kconfigs.rpm.RpmExtractor"
    assert distros[0].index == ("https://repo.example.com/1/x86_64/kernel-lts/")
    assert distros[0].key == "RPM-GPG-KEY-1-kernel-lts"


def test_get_distros_keeps_plain_sections_supported() -> None:
    cfg = parse_config(
        """
[plain]
name = Test Linux
version = 1
arch = x86_64
package = kernel
fetcher = kconfigs.rpm.RpmIndex
extractor = kconfigs.rpm.RpmExtractor
index = https://repo.example.com/
do_update = no
"""
    )

    distros = get_distros(cfg, [])

    assert len(distros) == 1
    assert distros[0].do_update is False
    assert distros[0].index == "https://repo.example.com/"


def test_get_distros_filter_matches_template_target_name() -> None:
    cfg = parse_config(
        """
[rpm:]
name = Test Linux $version $arch
package = kernel-core
fetcher = kconfigs.rpm.RpmIndex
extractor = kconfigs.rpm.RpmExtractor
index = https://repo.example.com/$version/$arch/

[rpm:test_1_x86_64]
version = 1
arch = x86_64

[rpm:test_1_aarch64]
version = 1
arch = aarch64
"""
    )

    distros = get_distros(cfg, ["test_*_aarch64"])

    assert len(distros) == 1
    assert distros[0].arch == "aarch64"


def test_get_distros_rejects_duplicate_names() -> None:
    cfg = parse_config(
        """
[one]
name = Duplicate
arch = x86_64
package = kernel
fetcher = kconfigs.rpm.RpmIndex
extractor = kconfigs.rpm.RpmExtractor
index = https://repo.example.com/one/

[two]
name = Duplicate
arch = aarch64
package = kernel
fetcher = kconfigs.rpm.RpmIndex
extractor = kconfigs.rpm.RpmExtractor
index = https://repo.example.com/two/
"""
    )

    with pytest.raises(
        ValueError,
        match=(
            r"sections \[one\] and \[two\] resolve to duplicate "
            r"distro name 'Duplicate'"
        ),
    ):
        get_distros(cfg, ["one"])


def test_get_distros_rejects_unknown_template_variable() -> None:
    cfg = parse_config(
        """
[rpm:]
name = Test Linux
package = kernel-core
fetcher = kconfigs.rpm.RpmIndex
extractor = kconfigs.rpm.RpmExtractor
index = https://repo.example.com/$missing/

[rpm:test_1_x86_64]
version = 1
arch = x86_64
"""
    )

    with pytest.raises(
        ValueError,
        match=(
            r"section \[rpm:test_1_x86_64\] field index references "
            r"unset variable \$missing"
        ),
    ):
        get_distros(cfg, [])


def test_get_distros_rejects_unknown_template() -> None:
    cfg = parse_config(
        """
[rpm:test_1_x86_64]
version = 1
arch = x86_64
"""
    )

    with pytest.raises(
        ValueError,
        match=r"section \[rpm:test_1_x86_64\] uses unknown template \[rpm:\]",
    ):
        get_distros(cfg, [])
