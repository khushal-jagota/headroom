# Signed desktop update proof

The private proof uses two signed, notarized builds from one manual run of `desktop.yml`. It publishes no GitHub Release. The normal release feed remains unchanged.

Dispatch the Ticket branch with `private_feed_url` set to `http://127.0.0.1:18769/` for a feed on the test Mac. Use the configured private HTTPS address for a feed on another machine. Do not put a token or password in the URL. The workflow embeds the address in both proof builds. Normal builds contain no override.

The workflow emits `panels-desktop-mac-proof-a` and `panels-desktop-mac-proof-b`. Each contains a signed, notarized DMG, a ZIP, a matching `latest-mac.yml`, and its blockmap. The DMG supplies first installation. The ZIP supplies updates. The workflow checks both the DMG and its app for signatures, notarization, and Gatekeeper acceptance. It mounts the DMG without Finder, checks its Applications shortcut, and copies the app with `ditto`. It also checks the app version and the manifest checksum. Build B has a higher version than build A.

Coordinate the proof on the assigned test Mac after both artifacts exist. Give the exact run URL, commit, and versions. The Manager coordinates the following work:

1. Download both artifacts into separate folders on the test Mac.
2. Serve a private folder through the configured address. Initially copy build A's files into that folder.
3. Install build A once, then open it.
4. Quit build A. Replace the feed's files with build B's files. Copy its ZIP and blockmap before its manifest. Reopen build A to trigger its startup check.
5. Check that the app downloads build B while its window stays usable.
6. Quit the app, reopen it, and check the new version and signature.
7. Repeat from build A for the no-window route. After the download, close the window and wait ten minutes. Check that the installed app is build B.
8. Repeat from build A and reopen its window before ten minutes. Check that the app waits for quit or another continuous no-window interval.
9. Restore the normal app after the proof. Stop the private feed.

Record the app versions before and after each route, the signature checks, updater logs, and screenshots of the real app. Use a failed or interrupted private download to check that build A still opens. These checks prove installation through macOS, which pure tests cannot prove.

The native compatibility dialog has a separate check. It requires the shared shell floor contract and a signed build that satisfies the new floor. A dialog must not quit the app before its update is ready or without consent.

A manual run can use a pushed Ticket branch through the workflow ID and `--ref`. GitHub must already know the workflow on its default branch for `workflow_dispatch`. If GitHub refuses that route, ask the Manager for the hosted build step. Do not publish a release or merge to main to enable the proof.

## Installer proof on Mac 1

Nothing can appear in front of Khushal. Do not open Finder or show a dialog. Mount the DMG with `hdiutil attach -readonly -nobrowse`, check its contents and signatures, and copy the app with `ditto`. Preserve the existing installed app. Use a separate test location for the copy. Detach the image after the check.

The move offer tests use a stubbed dialog. They cover acceptance, decline, failure, and startup without an offer for installed or development copies. If a real app launch is needed, use the approved background method from the desktop window Ticket, with `orderBack` and no dialog. Never launch a changed copy of the signed app. macOS checks the bundle before any app code runs, and a copy with changed files makes macOS show a "damaged" dialog. `orderBack` and in-app hooks hide only the app's own windows, not dialogs from macOS. Before you launch a signed `Panels.app` or any release bundle, run `spctl --assess --type execute -vv` on that exact bundle. If the check fails, do not launch it. To test released code, unpack the release's app files and run stock Electron at the release's Electron version against them. Do not copy or change `Panels.app` for this test. `spctl` rejects stock npm Electron as an ad hoc build, and that is expected. Instead, check three things before the launch: Electron has no `com.apple.quarantine` attribute, it is an unchanged npm install at the release's version, and it runs a plain app folder.

The real browser download, Finder drag installation, and move offer dialog remain Khushal's check after the first DMG release. Include a launch from Applications without translocation and the notification banner with Focus off. This Ticket does not publish that release.
