# Signed desktop update proof

The private proof uses two signed, notarized builds from one manual run of `desktop.yml`. It publishes no GitHub Release. The normal release feed remains unchanged.

Dispatch the Ticket branch with `private_feed_url` set to `http://127.0.0.1:18769/` for a feed on the test Mac. Use the configured private HTTPS address for a feed on another machine. Do not put a token or password in the URL. The workflow embeds the address in both proof builds. Normal builds contain no override.

The workflow emits `panels-desktop-mac-proof-a` and `panels-desktop-mac-proof-b`. Each contains a ZIP, a matching `latest-mac.yml`, and its blockmap. The workflow checks the signature, notarization, Gatekeeper assessment, app version, and the manifest checksum. Build B has a higher version than build A.

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
