# Archive runtime

A published Headroom release carries a private CPython 3.12. The sealed archive includes that interpreter’s standard library, its extension modules, and the non-glibc libraries those native files need. Adoption does not install Python packages and does not repair the archive. If the packager cannot resolve a library, that is a broken release, not something the host fixes at install time.

## What the host must be

The supported platforms are Debian 13 with glibc 2.41 or newer, and Ubuntu 24.04 with glibc 2.39 or newer, both Linux x86-64, with Node 22. The CPU policy is x86-64-v3. That is a support boundary, not a claim about every instruction in every vendor binary. A container uses the host’s CPU, so the image name alone does not prove the machine is allowed. Admission checks the declared processor features.

The host supplies the glibc loader and the libraries the runtime contract names. The payload supplies the rest, including ICU, libstdc++, libgcc, and libtinfo where they are required. Node and the shell stay outside the archive, with their own system packages. Admission runs Node to check that prerequisite. Hermes, media tools, and credentials stay operator-owned, outside this runtime.

## How a candidate is refused

The manifest embeds one versioned runtime contract. The same contract sits in the digest-covered payload at `runtime/contract.json` and binds `runtime/inventory.json`. Packaging refuses conflicting providers and required libraries it cannot find. Static analysis does not prove every dynamic load. A separate proof extracts the same archive into a minimal Debian root that has no Python 3.12 and no package installer, without network, credentials, or production mounts, and runs the launcher through an exact-SHA health check and an initialized API read.

Direct deployment and owner adoption use the same admission function, on the original candidate and on the staged copy, before any service action. A refusal does not stop the service, replace the app, migrate data, or take a backup.

A legacy manifest remains readable so the current app and a recovery fallback still make sense. A legacy candidate with no runtime contract cannot be installed. It has to be republished. An unknown contract version is refused too.

## The Tk omission

The archive leaves out the unused Tk extension and the Tcl/Tk libraries. That omission is specific. It does not allow any other missing native dependency. A dependency still has an import path that could reach Tk, and nothing in the product takes it. If a future change takes that path, the failure appears at runtime rather than at packaging time. The absence proof plus startup, admission, and publication checks are what bound that residual. Using those lazy aliases later needs a new packaging decision before the code ships.

## Where it shows up

An owner update that cannot admit the runtime fails before downtime and says why. Publication smoke and the disposable rehearsal consume the same sealed archive the runner installs. There is no separate “runtime update” in the product. The runtime arrives inside the app archive.

## Pointers

`src/planner/environments/runtime_packaging.py`, `runtime_admission.py`, and `runtime_elf.py`. The contract file inside a release is `runtime/contract.json`.

_Last verified: 2026-10-30._
