# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
import json

from kconfigs.model import Artifact
from kconfigs.model import IndexState


def test_index_state_json_round_trip_is_stable() -> None:
    state = IndexState(
        kind="kconfigs.rpm.RpmIndex",
        uid="https://example.com/repo/",
        data={
            "checksum": ("sha256", "abc123"),
            "primary_url": "https://example.com/repo/primary.sqlite.xz",
            "packages": ("kernel-core", "kernel-uek-core"),
        },
    )

    payload = state.to_json()

    assert payload == {
        "kind": "kconfigs.rpm.RpmIndex",
        "uid": "https://example.com/repo/",
        "data": {
            "checksum": ["sha256", "abc123"],
            "packages": ["kernel-core", "kernel-uek-core"],
            "primary_url": "https://example.com/repo/primary.sqlite.xz",
        },
    }

    encoded = json.dumps(payload, sort_keys=True)
    decoded = IndexState.from_json(json.loads(encoded))

    assert decoded == state
    assert json.dumps(decoded.to_json(), sort_keys=True) == encoded


def test_index_state_equality_includes_kind_uid_and_data() -> None:
    state = IndexState("kind", "uid", {"revision": "one"})

    assert state == IndexState("kind", "uid", {"revision": "one"})
    assert state != IndexState("other", "uid", {"revision": "one"})
    assert state != IndexState("kind", "other", {"revision": "one"})
    assert state != IndexState("kind", "uid", {"revision": "two"})


def test_artifact_json_round_trip_contains_source_index_state() -> None:
    state = IndexState(
        "kconfigs.rpm.RpmIndex",
        "https://example.com/repo/",
        {"checksum": ("sha256", "index")},
    )
    artifact = Artifact(
        url="https://example.com/kernel.rpm",
        checksum=("sha256", "package"),
        signature_url=None,
        source_index_state=state,
        version="1.0-1",
    )

    payload = artifact.to_json()

    assert payload == {
        "url": "https://example.com/kernel.rpm",
        "checksum": ["sha256", "package"],
        "signature_url": None,
        "source_index_state": state.to_json(),
        "version": "1.0-1",
    }
    assert Artifact.from_json(payload) == artifact


def test_artifact_same_download_as_ignores_source_index_state() -> None:
    old_state = IndexState("kind", "uid", {"revision": "old"})
    new_state = IndexState("kind", "uid", {"revision": "new"})
    artifact = Artifact(
        url="https://example.com/kernel.rpm",
        checksum=("sha256", "package"),
        signature_url="https://example.com/kernel.rpm.sig",
        source_index_state=old_state,
        version="1.0-1",
    )

    assert artifact.same_download_as(
        Artifact(
            url="https://example.com/kernel.rpm",
            checksum=("sha256", "package"),
            signature_url="https://example.com/kernel.rpm.sig",
            source_index_state=new_state,
            version="1.0-1",
        )
    )

    assert not artifact.same_download_as(
        Artifact(
            url="https://example.com/other.rpm",
            checksum=("sha256", "package"),
            signature_url="https://example.com/kernel.rpm.sig",
            source_index_state=old_state,
            version="1.0-1",
        )
    )
    assert not artifact.same_download_as(
        Artifact(
            url="https://example.com/kernel.rpm",
            checksum=("sha512", "package"),
            signature_url="https://example.com/kernel.rpm.sig",
            source_index_state=old_state,
            version="1.0-1",
        )
    )
    assert not artifact.same_download_as(
        Artifact(
            url="https://example.com/kernel.rpm",
            checksum=("sha256", "package"),
            signature_url=None,
            source_index_state=old_state,
            version="1.0-1",
        )
    )
    assert not artifact.same_download_as(
        Artifact(
            url="https://example.com/kernel.rpm",
            checksum=("sha256", "package"),
            signature_url="https://example.com/kernel.rpm.sig",
            source_index_state=old_state,
            version="1.0-2",
        )
    )
