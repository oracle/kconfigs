# Copyright (c) 2026, Oracle and/or its affiliates.
# Licensed under the terms of the GNU General Public License.
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias
from typing import cast

Checksum: TypeAlias = tuple[str, str]
JSONScalar: TypeAlias = str | int | float | bool | None
JSON: TypeAlias = JSONScalar | tuple["JSON", ...] | Mapping[str, "JSON"]
SerializedJSON: TypeAlias = (
    JSONScalar | list["SerializedJSON"] | dict[str, "SerializedJSON"]
)


def _type_name(value: object) -> str:
    return type(value).__name__


def _freeze_json(value: object) -> JSON:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON float values must be finite")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JSON] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(
                    f"JSON object keys must be str, not {_type_name(key)}"
                )
            frozen[key] = _freeze_json(item)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    raise TypeError(f"value is not JSON-serializable: {_type_name(value)}")


def _thaw_json(value: JSON) -> SerializedJSON:
    if isinstance(value, Mapping):
        return {key: _thaw_json(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be str, not {_type_name(value)}")
    return value


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be str or None, not {_type_name(value)}")
    return value


def _require_mapping(
    data: Mapping[str, object], key: str
) -> Mapping[str, object]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise TypeError(f"{key} must be an object, not {_type_name(value)}")
    for nested_key in value:
        if not isinstance(nested_key, str):
            raise TypeError(
                f"{key} object keys must be str, not {_type_name(nested_key)}"
            )
    return cast(Mapping[str, object], value)


def _checksum_from_json(value: object) -> Checksum | None:
    if value is None:
        return None
    if not isinstance(value, list | tuple):
        raise TypeError(f"checksum must be an array, not {_type_name(value)}")
    if len(value) != 2:
        raise ValueError("checksum must contain exactly two strings")
    algorithm, digest = value
    if not isinstance(algorithm, str) or not isinstance(digest, str):
        raise TypeError("checksum must contain exactly two strings")
    return (algorithm, digest)


@dataclass(frozen=True)
class IndexState:
    kind: str
    uid: str
    data: Mapping[str, JSON]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str):
            raise TypeError(f"kind must be str, not {_type_name(self.kind)}")
        if not isinstance(self.uid, str):
            raise TypeError(f"uid must be str, not {_type_name(self.uid)}")
        frozen_data = _freeze_json(self.data)
        if not isinstance(frozen_data, Mapping):
            raise TypeError("data must be a JSON object")
        object.__setattr__(self, "data", frozen_data)

    def to_json(self) -> dict[str, SerializedJSON]:
        return {
            "kind": self.kind,
            "uid": self.uid,
            "data": _thaw_json(self.data),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> IndexState:
        return cls(
            kind=_require_str(data, "kind"),
            uid=_require_str(data, "uid"),
            data=cast(Mapping[str, JSON], _require_mapping(data, "data")),
        )


@dataclass(frozen=True)
class Artifact:
    url: str
    checksum: Checksum | None
    signature_url: str | None
    source_index_state: IndexState
    version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.url, str):
            raise TypeError(f"url must be str, not {_type_name(self.url)}")
        object.__setattr__(self, "checksum", _checksum_from_json(self.checksum))
        if self.signature_url is not None and not isinstance(
            self.signature_url, str
        ):
            raise TypeError(
                "signature_url must be str or None, "
                f"not {_type_name(self.signature_url)}"
            )
        if not isinstance(self.source_index_state, IndexState):
            raise TypeError(
                "source_index_state must be IndexState, "
                f"not {_type_name(self.source_index_state)}"
            )
        if self.version is not None and not isinstance(self.version, str):
            raise TypeError(
                f"version must be str or None, not {_type_name(self.version)}"
            )

    def same_download_as(self, other: Artifact) -> bool:
        return (
            self.url == other.url
            and self.checksum == other.checksum
            and self.signature_url == other.signature_url
            and self.version == other.version
        )

    def to_json(self) -> dict[str, SerializedJSON]:
        checksum: SerializedJSON = None
        if self.checksum is not None:
            checksum = [self.checksum[0], self.checksum[1]]
        return {
            "url": self.url,
            "checksum": checksum,
            "signature_url": self.signature_url,
            "source_index_state": self.source_index_state.to_json(),
            "version": self.version,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> Artifact:
        return cls(
            url=_require_str(data, "url"),
            checksum=_checksum_from_json(data.get("checksum")),
            signature_url=_optional_str(data, "signature_url"),
            source_index_state=IndexState.from_json(
                _require_mapping(data, "source_index_state")
            ),
            version=_optional_str(data, "version"),
        )
