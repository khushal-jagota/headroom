"""Immutable, digest-bound contract for private archive runtimes.

The manifest embeds this contract. The identical object also lives in the
artifact-digested payload. Its inventory hash binds the detailed native audit.
Host Node and the shell are prerequisites, never providers for Python libraries.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from re import fullmatch

RUNTIME_CONTRACT_VERSION = 1
RUNTIME_CONTRACT_PATH = "runtime/contract.json"
RUNTIME_INVENTORY_PATH = "runtime/inventory.json"
# Conservative supported processor boundary, not a claim about vendor minima.
SUPPORTED_CPU_FEATURES = (
    "avx",
    "avx2",
    "bmi1",
    "bmi2",
    "cx16",
    "f16c",
    "fma",
    "lahf_lm",
    "abm",
    "movbe",
    "popcnt",
    "sse2",
    "sse4_1",
    "sse4_2",
    "ssse3",
    "xsave",
)
GLIBC_LIBRARIES = (
    "ld-linux-x86-64.so.2",
    "libc.so.6",
    "libdl.so.2",
    "libm.so.6",
    "libpthread.so.0",
    "libresolv.so.2",
    "librt.so.1",
    "libutil.so.1",
)


class RuntimeContractError(ValueError):
    """The runtime contract is malformed or unsupported."""


@dataclass(frozen=True)
class RuntimePlatform:
    distribution: str
    version: str
    minimum_glibc: str

    def as_dict(self) -> dict[str, str]:
        return {
            "distribution": self.distribution,
            "version": self.version,
            "minimum_glibc": self.minimum_glibc,
        }


SUPPORTED_PLATFORMS = (
    RuntimePlatform("debian", "13", "2.41"),
    RuntimePlatform("ubuntu", "24.04", "2.39"),
)


def canonical_json_bytes(value: object) -> bytes:
    """Canonical JSON encoding used for contract and inventory identities."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def runtime_bytes_digest(value: bytes) -> str:
    """Hash exact inventory bytes, including any trailing newline."""
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class RuntimeContract:
    python_version: str
    inventory_sha256: str
    version: int = RUNTIME_CONTRACT_VERSION
    system: str = "linux"
    architecture: str = "x86_64"
    platforms: tuple[RuntimePlatform, ...] = SUPPORTED_PLATFORMS
    node_major: int = 22
    cpu_features: tuple[str, ...] = SUPPORTED_CPU_FEATURES
    external_libraries: tuple[str, ...] = GLIBC_LIBRARIES
    external_executables: tuple[str, ...] = ("sh", "node")

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != RUNTIME_CONTRACT_VERSION:
            raise RuntimeContractError("unsupported runtime contract version; republish the app")
        if self.system != "linux" or self.architecture != "x86_64":
            raise RuntimeContractError("unsupported runtime operating system or architecture")
        if self.platforms != SUPPORTED_PLATFORMS:
            raise RuntimeContractError("unsupported runtime platform boundary")
        if type(self.node_major) is not int or self.node_major != 22:
            raise RuntimeContractError("runtime requires host Node major 22")
        if fullmatch(r"3\.12\.\d+", self.python_version) is None:
            raise RuntimeContractError("runtime requires a concrete CPython 3.12 patch version")
        if fullmatch(r"[0-9a-f]{64}", self.inventory_sha256) is None:
            raise RuntimeContractError("invalid runtime inventory SHA256")
        if self.external_libraries != GLIBC_LIBRARIES:
            raise RuntimeContractError("non-glibc runtime libraries must be bundled")
        if self.external_executables != ("sh", "node"):
            raise RuntimeContractError("unsupported external runtime executables")
        if (
            not isinstance(self.cpu_features, tuple)
            or not set(SUPPORTED_CPU_FEATURES) <= set(self.cpu_features)
            or len(set(self.cpu_features)) != len(self.cpu_features)
            or any(fullmatch(r"[a-z0-9_]+", feature) is None for feature in self.cpu_features)
        ):
            raise RuntimeContractError("invalid required CPU features")

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "system": self.system,
            "architecture": self.architecture,
            "platforms": [platform.as_dict() for platform in self.platforms],
            "python_version": self.python_version,
            "node_major": self.node_major,
            "cpu_features": list(self.cpu_features),
            "external_libraries": list(self.external_libraries),
            "external_executables": list(self.external_executables),
            "inventory_sha256": self.inventory_sha256,
        }

    @property
    def digest(self) -> str:
        return runtime_bytes_digest(canonical_json_bytes(self.as_dict()))

    @classmethod
    def from_dict(cls, value: object) -> RuntimeContract:
        if not isinstance(value, dict):
            raise RuntimeContractError("runtime contract must be an object")
        required = {
            "version",
            "system",
            "architecture",
            "platforms",
            "python_version",
            "node_major",
            "cpu_features",
            "external_libraries",
            "external_executables",
            "inventory_sha256",
        }
        if set(value) != required:
            raise RuntimeContractError("runtime contract has missing or unknown fields")
        platforms = value["platforms"]
        if platforms != [platform.as_dict() for platform in SUPPORTED_PLATFORMS]:
            raise RuntimeContractError("unsupported runtime platform boundary")
        for key in ("version", "node_major"):
            if type(value[key]) is not int:
                raise RuntimeContractError(f"runtime {key} must be an integer")
        for key in ("system", "architecture", "python_version", "inventory_sha256"):
            if not isinstance(value[key], str):
                raise RuntimeContractError(f"runtime {key} must be a string")
        for key in ("cpu_features", "external_libraries", "external_executables"):
            if not isinstance(value[key], list) or not all(
                isinstance(item, str) for item in value[key]
            ):
                raise RuntimeContractError(f"runtime {key} must be a list of strings")
        return cls(
            version=value["version"],
            system=value["system"],
            architecture=value["architecture"],
            platforms=SUPPORTED_PLATFORMS,
            python_version=value["python_version"],
            node_major=value["node_major"],
            cpu_features=tuple(value["cpu_features"]),
            external_libraries=tuple(value["external_libraries"]),
            external_executables=tuple(value["external_executables"]),
            inventory_sha256=value["inventory_sha256"],
        )


def parse_optional_runtime_contract(value: object) -> RuntimeContract | None:
    """Legacy manifests stay readable; candidate admission must reject None."""
    return None if value is None else RuntimeContract.from_dict(value)
