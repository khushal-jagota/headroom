"""Digest-bound native Mac home runtime contract."""

from __future__ import annotations

from dataclasses import dataclass

from planner.environments.mac_runtime_configuration import MAC_RUNTIME_MINIMUM_OS


@dataclass(frozen=True)
class DarwinRuntimeContract:
    python_version: str
    inventory_sha256: str
    version: int = 1
    system: str = "darwin"
    architecture: str = "arm64"
    role: str = "home"
    minimum_os: str = MAC_RUNTIME_MINIMUM_OS
    node_major: int = 22
    external_libraries: tuple[str, ...] = ("/usr/lib/", "/System/Library/")

    def __post_init__(self) -> None:
        import re

        if (
            type(self.version) is not int
            or self.version != 1
            or self.system != "darwin"
            or self.architecture != "arm64"
            or self.role != "home"
        ):
            raise ValueError("runtime requires a darwin arm64 home artifact")
        if self.minimum_os != MAC_RUNTIME_MINIMUM_OS:
            raise ValueError("unsupported Mac runtime minimum OS")
        if type(self.node_major) is not int or self.node_major < 22:
            raise ValueError("runtime requires host Node major 22 or newer")
        if re.fullmatch(r"3\.12\.\d+", self.python_version) is None:
            raise ValueError("runtime requires a concrete CPython 3.12 patch version")
        if re.fullmatch(r"[0-9a-f]{64}", self.inventory_sha256) is None:
            raise ValueError("invalid runtime inventory SHA256")
        if self.external_libraries != ("/usr/lib/", "/System/Library/"):
            raise ValueError("non-system Mac runtime libraries must be bundled")

    def as_dict(self) -> dict[str, object]:
        from dataclasses import asdict

        value = asdict(self)
        value["external_libraries"] = list(self.external_libraries)
        return value

    @property
    def digest(self) -> str:
        from planner.platform_runtime.linux.runtime_contracts import (
            canonical_json_bytes,
            runtime_bytes_digest,
        )

        return runtime_bytes_digest(canonical_json_bytes(self.as_dict()))

    @classmethod
    def from_dict(cls, value: object) -> DarwinRuntimeContract:
        from dataclasses import fields

        if not isinstance(value, dict) or set(value) != {item.name for item in fields(cls)}:
            raise ValueError("Mac runtime contract has missing or unknown fields")
        strings = (
            "python_version",
            "inventory_sha256",
            "system",
            "architecture",
            "role",
            "minimum_os",
        )
        if any(not isinstance(value[key], str) for key in strings):
            raise ValueError("Mac runtime contract identity fields must be strings")
        if value["external_libraries"] != ["/usr/lib/", "/System/Library/"]:
            raise ValueError("non-system Mac runtime libraries must be bundled")
        return cls(
            python_version=value["python_version"],
            inventory_sha256=value["inventory_sha256"],
            version=value["version"],
            system=value["system"],
            architecture=value["architecture"],
            role=value["role"],
            minimum_os=value["minimum_os"],
            node_major=value["node_major"],
        )
