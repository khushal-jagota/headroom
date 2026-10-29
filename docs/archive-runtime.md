# Executable archive runtime

New releases carry a private CPython 3.12 runtime. The archive contains its standard
library, extension modules, and the non-glibc libraries that its native files require.
Adoption does not install dependencies or repair the archive.

The supported application platforms are Debian 13 with glibc 2.41 or newer, and
Ubuntu 24.04 with glibc 2.39 or newer. Both use Linux x86-64 and Node 22.
The supported CPU boundary is x86-64-v3. This is a conservative support policy,
not a claim about the minimum instructions in every vendor binary.
Admission checks the declared processor features and bounded native operations.
A container shares its host CPU, so the target image alone does not prove CPU support.

The host supplies the glibc loader and libraries listed in the runtime contract.
The payload supplies other libraries used by its native files, including ICU,
libstdc++, libgcc, and libtinfo where required. Existing wheel-private library
directories retain their relative lookup rules. Host Node and the shell remain
external executables with their own system dependencies. Admission executes Node
to check that prerequisite. The deployment service, Hermes installation, optional
media tools, and credentials keep their existing operator-owned contracts.

The manifest embeds one versioned runtime contract. The same contract exists in
the digest-covered payload at `runtime/contract.json`. It binds the full native
inventory at `runtime/inventory.json`. The inventory records file identities,
providers, copied component origins, licenses, CPU notes, and exclusions.
Static dependency analysis does not prove dynamic loads. Target execution supplies
the separate evidence for those operations.

Publication builds once and seals once. The existing smoke and replacement checks
consume that archive. An additional proof extracts the same archive into a minimal
Debian root with no Python 3.12, ICU 74, package installers, or builder paths.
The final root inventory and source image digest identify the proof environment.
The proof runs without network access, provider credentials, or production mounts.
It covers native libraries, runtime entrypoints, relocation, the installed launcher,
exact-SHA health, referenced assets, and an initialized API read. Its evidence binds
the archive, application, contract, and target root identities and records duration.

Both direct deployment and owner adoption use the same admission function.
It checks the original candidate and the staged copy before any service action.
This applies to first install and upgrades. A refusal does not stop or restart
the service, replace the current application, migrate data, or run a backup.
The probes use temporary state, an environment allowlist, and bounded process groups.

Legacy manifests remain readable for the current application and recovery fallback.
A legacy candidate without the runtime contract cannot authorize a new install.
It requires republication. Unknown contract versions also refuse admission.
Pending legacy requests remain readable for inspection and cancellation.

The existing full source suites, replacement checks, and recovery checks remain
publication requirements. This correction introduces no deliberate rollback exercise.
