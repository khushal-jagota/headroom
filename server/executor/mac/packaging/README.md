# Mac runtime package producer

`build.ts` assembles an exported source tree, a supplied Darwin arm64 Node distribution,
production dependencies from `npm ci --omit=dev`, and a signed launcher app.
The payload omits only node-gyp’s generated `better-sqlite3/build/Makefile` and `config.gypi`.
These build inputs contain installation paths. Runtime native addons remain in the payload and content identity. It downloads and executes none of them.
It writes a new output directory and leaves any failed partial output for inspection.

`runtime/` is the release payload. Its `mac-runtime.json` identifies the revision, target, and machine content build.
`bin/panels` uses that payload's Node and entrypoint after relocation. The wire schema stays embedded.
The payload keeps any driver's own scripts, static skill/Worker seed data, version metadata,
server JSON/bash assets and complete production packages. It contains no Python interpreter.
`launcher/Panels TypeScript.app` is the launcher that every account shares, so the archive digest covers it.
The package holds no account path. A join writes the account's console and LaunchAgent on the machine.
The signed launcher remains alive while its Node child runs, forwards termination signals, and reaps the child's exit.
Worker mode preserves its release command and environment. Executor and console modes use the fixed supervisor command.

`launcher_main.ts` compiles the launcher with `clang` and signs it. The release workflow signs it with the
project's Developer ID. The pull-request rehearsals pass `-` for an ad-hoc signature.

The one synthetic check uses structural Mach-O headers. It proves layout and a wrong-platform refusal,
not executable provenance, native addon loading, signing or a genuine Darwin runtime.

The release workflow calls `publication_main.ts`. It unpacks the sealed archive at a separate path and uses the bundled Node for CLI and native checks.
It also checks the signature of the unpacked launcher. The publication evidence records the signed launcher tree hash.
The Manager chose the signed launcher as a fixed external input, like Node. Repeat builds use that same checked launcher
and independent locked dependency installations. The repeat assembly verifies the machine identity. Only the first assembly produces a release archive. The content build identity includes every launcher byte.
The native probe loads SQLite and the Mac host and copies verified bytes through the Darwin file worker.
Only a successful proof produces `mac-runtime-publication.json`, bound to the source, workflow run and attempt, and archive bytes.
The update and join artifact contains only the archive and receipt.

The release rehearsal runs the server's join command on a clean hosted Mac with the candidate machine archive.
It checks the actual launchd job and launcher recovery. A manual run also exercises the packaged runtime against a private TypeScript home with scripted Claude and Codex CLIs.
Folder consent and permission retention under the Developer ID remain checks on a real second machine.

The file-transfer part uses a test double of the home file routes because home file custody requires Linux procfs. It checks the packaged Mac transport and exact bytes and digests. Real Linux home file intake remains a live check after the Mac switch.
