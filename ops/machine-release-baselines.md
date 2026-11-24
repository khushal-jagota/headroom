# Retained machine archives

Publication checks use retained machine archives to prove that an old machine can update and that the previous build can work with the candidate server.

The signed Darwin launcher is a pinned external input, like the Node distribution. Reproducibility checks reuse its checked bytes and prepare the dependency trees independently. The machine identity includes the complete launcher contents, so a different pinned launcher changes the identity. Do not remove its signature bytes from the content hash or sign it again for the repeat check.

The supported floor, the "oldest" baseline, is the release `e71baa53c98350a7b5519b0106e1c3e57a09382d`, from publication run `44332149248`. Do not advance the supported floor merely because a new release exists.

The "previous" baseline is the build that machines run now. Until the first permanent server Release exists, `server/environments/release/machine_baselines.json` pins it next to the floor. After that, the newest permanent Release supplies it. Advance the pinned previous baseline after each successful publication until then. It names the live release `0cde68a66080834c8df719fba8af0650585e5e59`, from publication run `44936271164` (a push to `main`), which Mac 1's machine code runs.

Before the first permanent Release, the checked Actions artifacts supply the baseline. Hosted checks retain a digest-pinned copy in the CI cache. A cache can evict entries. The private recovery copy on VPS 1 lives at:

```text
/home/vps/.local/share/panels/machine-release-baselines/e71baa53c98350a7b5519b0106e1c3e57a09382d/
  publication-proof.zip
  darwin.zip
  linux.zip
  custody.json
```

The directory is private to the VPS account. The files have mode `0600`. This store is outside Ticket cleanup. The gate reads the pinned baseline contract, not the live installation. The recovery copy preserves the same bytes when Actions retention or cache eviction removes a hosted copy.

| Input | Actions artifact | ZIP SHA-256 |
| --- | --- | --- |
| Publication receipt | 14441515932 | be7f93a5990453923f3f2504072dfbd572f3e3a4a96e8037f880c4401b6de556 |
| Darwin machine | 14440850827 | 4b09249dd32c39944959c1f4c97ec5c2ee3887f9130832625768967a4befe8cf |
| Linux machine | 14440291876 | 6aa74dfdbb7149b6031ac6259883f2cecfbb36b2e7e5817a10c43a458ff010e4 |

The receipt records these inner archive checksums:

| Archive | SHA-256 | Bytes |
| --- | --- | --- |
| panels-mac-runtime.tar.gz | b535f1f8e6802a3876e81c0ff5e1470f7f779d2f10b1286684868305910ab86c | 222964142 |
| panels-linux-executor.tar.gz | 6b1ef9bda87e9b8d0d38685233b387e7f3628c608fbca851966db730dbb144ca | 571145210 |

Check both the ZIP digest and the inner archive digest before use or recovery. Do not substitute another archive after a failure. A missing baseline blocks publication.

The pinned previous baseline comes from that release's own publication. Its ZIP digests are the GitHub artifact digests. Its archive checksums are the ones in `publication.json` of the run's proof artifact `14719719938`, and the live update record of 23 November 2026 holds the same values. Each archive's `mac-runtime.json` names `0cde68a6` and its platform. The artifacts expire on 21 December 2026. They need no recovery copy, because the first permanent Release replaces them.

| Input | Actions artifact | ZIP SHA-256 |
| --- | --- | --- |
| Darwin machine | 14720025420 | d8209eefd15c6892c036f58465b0a4335c7857350439e75c283f0a6a165580ad |
| Linux machine | 14719479187 | 0be8aaeba8fff9e4180f6d7b5fe68c77560a7dca4f7c1e9401ff2dbd4f65def3 |

| Archive | SHA-256 | Bytes |
| --- | --- | --- |
| panels-mac-runtime.tar.gz | e73d299aa88a8d9322d9c77d536116ec24c92bc80395f733d460e29812a12f01 | 222969537 |
| panels-linux-executor.tar.gz | 1edcc94649eaf3294ceb75bff03e2f3850aa970a557ca35dee52cc4210e8d4ef | 578507231 |

The first permanent Release requires Khushal's release to `main`. Do not publish a public Release to store these inputs. After that Release exists, move the baseline source to its permanent assets through an explicit follow-up.

After two weeks of publication history, the Manager files a report of how often releases change each machine build. Include the observation window and sample size. The report must precede any general promise that agents keep running through an update.
