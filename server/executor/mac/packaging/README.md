# Mac runtime package producer

`main.ts` assembles an exported source tree, a supplied Darwin arm64 Node distribution,
and complete production dependencies from `npm ci --omit=dev`. It downloads and executes none of them.
It writes a new output directory and leaves any failed partial output for inspection.

`runtime/` is the release payload. Its four-field `mac-runtime.json` identifies the revision and target.
`bin/panels` uses that payload's Node and entrypoint after relocation. The wire schema stays embedded.
The payload keeps plain Hermes provider scripts, static skill/Worker seed data, version metadata,
server JSON/bash assets and complete production packages. It contains no Python server or Python interpreter.

`assets/` holds the unsigned candidate app metadata, C requester source, manual console and LaunchAgent plist.
The fixed supervisor installs separately at the paths in `assets/installation.json`.
Versioned runtimes live under the state's `updates/releases/<revision>-<archive-sha256>`.
The producer never compiles, signs, installs, changes launchd or starts providers.

The one synthetic check uses structural Mach-O headers. It proves layout and a wrong-platform refusal,
not executable provenance, native addon loading, signing or a genuine Darwin runtime.
Those proofs require the supplied native inputs and a Manager-owned Darwin CI caller.
