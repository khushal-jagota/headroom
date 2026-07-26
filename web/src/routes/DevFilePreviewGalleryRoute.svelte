<script lang="ts">
  /** Every variant the file-preview system can produce, side by side.
   *
   * Every tile below is the real `FilePreview` component (or, where noted, the real
   * `MarkdownBlock` a chat message renders through) resolving a real target. Nothing
   * here hand-draws what a tile would look like — the "not there" tiles point at paths
   * that were deliberately never written under the fixture ticket's files, so their
   * broken state is the component meeting a real 404, not a mockup of one.
   *
   * Not in the nav, and nothing the app does links here.
   */
  import FilePreview from "../components/FilePreview.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import ScreenHeader from "../components/ScreenHeader.svelte";
  import SectionHeading from "../components/SectionHeading.svelte";
  import type { FilePreviewTarget } from "../lib/filePreview";
  import { ticketFileTarget } from "../lib/filePreview";

  // A ticket created on this worktree's own dogfood server purely to hold these
  // fixture files. Both the ticket and its `data/dogfood-owner/files/tickets/...`
  // directory are gitignored runtime state, not part of this change's diff.
  const FIXTURE_TICKET_ID = "t_nbx2y7rk";

  function ticketFile(path: string): FilePreviewTarget {
    const target = ticketFileTarget(FIXTURE_TICKET_ID, path);
    if (!target) throw new Error(`fixture path is not a valid ticket-file target: ${path}`);
    return target;
  }

  const externalLink = (href: string, label: string): FilePreviewTarget => ({
    kind: "external-link",
    href,
    label
  });

  // Rendered exactly the way a conversation transcript renders a message: `MarkdownBlock`
  // at the top, depth 0, nothing visited yet.
  const imagesMessage = `An image from this ticket's own files:

![Test pattern](/files/tickets/${FIXTURE_TICKET_ID}/media/pattern.png)

An image from another origin, on a host that serves no such file:

![External placeholder](https://example.com/placeholder.png)

An image wrapped in a link:

[![Test pattern](/files/tickets/${FIXTURE_TICKET_ID}/media/pattern.png)](https://example.com)`;

  const linksMessage = `- [A heading on this page](#results) — a same-page anchor.
- [The Day screen](#/day) — this app's own route.
- [Another site](https://example.com) — an off-site address.
- [A managed ticket file](/files/tickets/${FIXTURE_TICKET_ID}/code/coding.py) — the one
  shape the preview system claims.`;
</script>

<section class="gallery-screen" data-dev-file-preview-gallery>
  <ScreenHeader title="File preview — variant gallery" />
  <p class="gallery-intro">
    Every kind the file-preview system resolves, mounted as the real component against real
    files. Fixtures live under a throwaway ticket (<code>{FIXTURE_TICKET_ID}</code>) on this
    worktree's own server.
  </p>

  <SectionHeading label="Things with something to show" />
  <div class="gallery-tiles">
    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Image</span>
        <span class="gallery-tile-note">media/pattern.png — an ffmpeg test pattern.</span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("media/pattern.png")} />
      </div>
    </figure>

    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Image — SVG</span>
        <span class="gallery-tile-note">icons/favicon.svg — this repository's real favicon.</span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("icons/favicon.svg")} />
      </div>
    </figure>

    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Video</span>
        <span class="gallery-tile-note">media/clip.mp4 — a real, playable clip.</span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("media/clip.mp4")} />
      </div>
    </figure>

    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Audio</span>
        <span class="gallery-tile-note">media/tone.wav — a 440Hz tone.</span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("media/tone.wav")} />
      </div>
    </figure>

    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Markdown</span>
        <span class="gallery-tile-note">
          docs/backups.md — a real doc, copied verbatim. The header stays put and the doc
          scrolls under it.
        </span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("docs/backups.md")} />
      </div>
    </figure>

    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">HTML</span>
        <span class="gallery-tile-note">
          pages/sample.html — a small static document in its sandboxed frame.
        </span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("pages/sample.html")} />
      </div>
    </figure>
  </div>

  <SectionHeading label="Everything else is a line" />
  <p class="gallery-intro">
    No card and no container — a line of text where it was written. Only markdown and HTML
    are fetched, so only they can say a file is not there.
  </p>
  <div class="gallery-tiles">
    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Download</span>
        <span class="gallery-tile-note">code/coding.py — no kind above claims ".py".</span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("code/coding.py")} />
      </div>
    </figure>

    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">A URL handed over directly</span>
        <span class="gallery-tile-note">
          An agent's resource link, which has a URL and nothing else to show.
        </span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={externalLink("https://example.com", "Example site")} />
      </div>
    </figure>

    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Markdown that is not there</span>
        <span class="gallery-tile-note">
          notes/does-not-exist.md was never written. The fetch finds out and the line says
          what it found.
        </span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("notes/does-not-exist.md")} />
      </div>
    </figure>

    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Image that is not there</span>
        <span class="gallery-tile-note">
          media/does-not-exist.png was never written. Nothing checks an image, so this is
          the browser's own broken-image mark.
        </span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("media/does-not-exist.png")} />
      </div>
    </figure>
  </div>

  <SectionHeading label="Inside a message" />
  <p class="gallery-intro">
    Every image is previewed, wherever it is hosted. Every link stays a link unless it names
    a managed file, and an image inside a link that stays a link is left standing with it.
  </p>
  <div class="gallery-tiles gallery-tiles--wide">
    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Three image shapes</span>
      </figcaption>
      <div class="gallery-tile-body">
        <MarkdownBlock text={imagesMessage} />
      </div>
    </figure>

    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">Four link shapes</span>
      </figcaption>
      <div class="gallery-tile-body">
        <MarkdownBlock text={linksMessage} />
      </div>
    </figure>
  </div>

  <SectionHeading label="Markdown inside markdown" />
  <p class="gallery-intro">
    One real chain of three ticket documents, mounted once. Level 0 expands level 1 inline;
    level 1's link to level 2 lands past the two-level cap and collapses to a line, and level
    0's link back to itself is caught by the cycle guard the same way.
  </p>
  <div class="gallery-tiles gallery-tiles--wide">
    <figure class="gallery-tile">
      <figcaption class="gallery-tile-caption">
        <span class="gallery-tile-label">notes/recursion/level-0.md</span>
      </figcaption>
      <div class="gallery-tile-body">
        <FilePreview target={ticketFile("notes/recursion/level-0.md")} />
      </div>
    </figure>
  </div>
</section>

<style>
  .gallery-screen {
    max-width: var(--measure-index);
    margin: 0 auto;
    padding: var(--space-6) var(--space-4) var(--space-page-tail);
  }
  .gallery-intro {
    margin: var(--space-2) 0 var(--space-5);
    color: var(--text-faint);
    font-size: var(--type-sm);
    max-width: var(--measure-read);
  }
  .gallery-tiles {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(20rem, 1fr));
    gap: var(--space-4);
    margin: var(--space-3) 0 var(--space-6);
  }
  .gallery-tiles--wide {
    grid-template-columns: minmax(0, 1fr);
  }
  .gallery-tile {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    min-width: 0;
    margin: 0;
    padding: var(--space-3);
    /* The page surface rather than a raised one, so a preview is judged against the
       surface it actually lands on. */
    background: var(--surface-base);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-md);
  }
  .gallery-tile-caption {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .gallery-tile-label {
    color: var(--text-strong);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }
  .gallery-tile-note {
    color: var(--text-faint);
    font-size: var(--type-xs);
  }
  .gallery-tile-body {
    min-width: 0;
  }
</style>
