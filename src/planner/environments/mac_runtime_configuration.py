"""Immutable upstream interpreter used by the native Mac publication builder.

The install-only distribution is relocatable, unlike a copied virtualenv.
Source: https://github.com/astral-sh/python-build-standalone/releases/tag/20261026
"""

MAC_PYTHON_VERSION = "3.12.16"
MAC_PYTHON_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/"
    "cpython-3.12.16%2B20261026-aarch64-apple-darwin-install_only.tar.gz"
)
MAC_PYTHON_SHA256 = "fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e"

# Build tools stay in an independent builder environment, outside the archive.
# Runtime/transitive pins still come from requirements.txt of the requested SHA.
MAC_BUILD_TOOL_REQUIREMENTS = (
    "setuptools==82.0.1",
    "wheel==0.46.3",
    "packaging==26.2",
    "typing_extensions==4.16.0",
    "delocate==0.13.0",
    "macholib==1.16.4",
    "altgraph==0.17.5",
)
MAC_RUNTIME_RECEIPT_NAME = "mac-runtime-publication.json"
MAC_RUNTIME_MINIMUM_OS = "15.0"

# Build ICU privately at the supported target. Host Homebrew bottles can require
# a newer macOS than the executor and home runtime permit.
MAC_ICU_VERSION = "79.1"
MAC_ICU_SOURCE_URL = (
    "https://github.com/unicode-org/icu/releases/download/release-79.1/"
    "icu4c-79.1-sources.tgz"
)
MAC_ICU_SOURCE_SHA256 = "06fa63ecb521048f8082f12ad988f6b678d90cbd88aafce24cbc94902b191d99"
MAC_ICU_BUILD_JOBS = 2
