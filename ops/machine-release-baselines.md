# Retained machine archives

Publication checks use retained machine archives to prove that an old machine can update and that the previous build can work with the candidate server.

The signed Darwin launcher is a pinned external input, like the Node distribution. Reproducibility checks reuse its checked bytes and prepare the dependency trees independently. The machine identity includes the complete launcher contents, so a different pinned launcher changes the identity. Do not remove its signature bytes from the content hash or sign it again for the repeat check.

On November 25, 2026, Khushal raised the supported floor, the “oldest” baseline, to the first permanent server Release:

`server-54aede8564ee693d178cae55d3acb04ca9d770f4-45315260518-1`

Machines below commit `54aede8564ee693d178cae55d3acb04ca9d770f4` no longer update directly. A new Release does not advance this floor automatically.

`machine_baselines.json` records the decision and pins each floor archive. The loader reads `mac.zip` and `linux-executor.zip` from this permanent Release. It compares the Release receipt and asset metadata with the pins. It checks the ZIP digest, inner archive digest, and archive size before use.

The “previous” baseline comes from the newest published, non-prerelease `server-*` Release. Its receipt and asset digest supply the same archive checks. Both the compatibility gate and the Mac launcher rehearsal use this Release loader. A missing Release or a digest mismatch blocks the check. No Actions artifact, CI cache, or private-store fallback supplies a baseline.

| Platform | ZIP SHA-256 | Inner archive SHA-256 | Archive bytes |
| --- | --- | --- | --- |
| Linux | cb7ea2cc0066f8e3f79ba117000964b73aacc5e5d8e60e173a8c4e183ff19438 | 146461f000c8ace079c8226ce1d9e28f42f12aa7b1f16ca2f6d7869c58db1bda | 945735823 |
| Darwin | f7bda08b417b18914b5b31bd55ef5cb0ed7f9655f2cd3dcc8ca4d2e5d6afebc8 | f311967566c532e69050ca462e667ad0c256f4d96b40b4ccf6a7ca02a5a15976 | 223121569 |

Retire the e71baa53 private copies only after the Linux and Darwin compatibility gates pass against the new floor. Both platform archives and their receipt live on VPS 1 at:

```text
/home/vps/.local/share/panels/machine-release-baselines/e71baa53c98350a7b5519b0106e1c3e57a09382d/
```

The Manager confirmed that no Mac custody copy exists. Remove the obsolete CI baseline cache entries after the gates pass. Record the retirement evidence in the Ticket.

After two weeks of publication history, the Manager files a report of how often releases change each machine build. Include the observation window and sample size. The report must precede any general promise that agents keep running through an update.
