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
