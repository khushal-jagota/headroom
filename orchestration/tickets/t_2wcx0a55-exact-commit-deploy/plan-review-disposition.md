# Independent plan review disposition — t_2wcx0a55

The independent read-only review found five blockers. All are corrected in the contract, plan, and
dispatch before implementation:

1. **Backup still read Git state from live.** Deployment now reads the prior revision only from the
   validated current release manifest and removes `PANELS_LIVE_REPOSITORY` assumptions.
2. **Exact GitHub source acquisition was underspecified.** The workflow must check out
   `github.sha`, verify the full checkout `HEAD`, and carry that same SHA end to end.
3. **Runtime identity had no authoritative source.** The validated release manifest now owns the
   production SHA supplied by the launcher, returned by `/api/meta`, and required by health proof.
4. **Service permissions were too abstract.** Platform inputs now name the read-only release root,
   external writable state paths, separate deploy/live identities, and Closeout enforcement proof.
5. **Canonical verifier ownership conflicted.** The implementer runs focused gates; the parent runs
   the one final canonical `./verify` after independent implementation review.

No source implementation began before these corrections.
