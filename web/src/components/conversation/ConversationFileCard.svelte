<script lang="ts">
  /** A file in a message. A picture or a video is drawn as itself (the shared preview, which
   *  opens the viewer). Any other file is a compact card (design v3 §3.9): a glyph tile, the
   *  name, what kind of file it is and its size, Preview when the browser can show it, and
   *  Download. It never unrolls the file's contents into the thread. */
  import FilePreview from "../FilePreview.svelte";
  import { conversationFileTarget, resolvePreview } from "../../lib/filePreview";
  import { attachmentByteLabel } from "./composer/attachments";
  import { fileOpensAsText, friendlyFileType } from "../../lib/conversation/fileTypes";

  let {
    href,
    fileName,
    byteCount,
    mediaType = ""
  }: {
    href: string;
    fileName: string;
    byteCount: number;
    mediaType?: string;
  } = $props();

  let target = $derived(conversationFileTarget(href, fileName));
  let media = $derived(target !== null && ["image", "video"].includes(resolvePreview(target).kind));
  let meta = $derived(`${friendlyFileType(fileName, mediaType)} · ${attachmentByteLabel(byteCount)}`);
  // A stored file opens in place, as the browser shows it. A file not yet stored (its bytes
  // are still in the page) has no address to open, so it offers only Download.
  let previewable = $derived(!href.startsWith("data:") && fileOpensAsText(fileName, mediaType));
</script>

{#if target && media}
  <article class="conversation-file-card conversation-file-card--media" data-conversation-piece="file" data-conversation-file-name={fileName}>
    <FilePreview {target} mode="embedded" knownSizeLabel={attachmentByteLabel(byteCount)} download={{href, fileName}} />
  </article>
{:else if target}
  <article class="conversation-file-card cv-file" data-conversation-piece="file" data-conversation-file-name={fileName}>
    <span class="cv-file-tile" aria-hidden="true">
      <svg viewBox="0 0 20 20"><path d="M5.5 2.75h6l3.75 3.75v10a.75.75 0 0 1-.75.75H5.5a.75.75 0 0 1-.75-.75V3.5a.75.75 0 0 1 .75-.75Z" /><path d="M11.25 2.75V6.5h4" /><path d="M7.5 10.25h5M7.5 13h3.5" /></svg>
    </span>
    <span class="cv-file-identity">
      <span class="cv-file-name" title={fileName}>{fileName}</span>
      <span class="cv-file-meta" data-conversation-file-meta>{meta}</span>
    </span>
    {#if previewable}
      <a class="cv-file-preview" {href} rel="noopener noreferrer" data-conversation-file-preview aria-label={`Preview ${fileName}`}>Preview</a>
    {/if}
    <a class="cv-file-download" {href} download={fileName} title="Download" aria-label={`Download ${fileName}`} data-conversation-file-download>
      <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.75v7.5M4.75 7.25 8 10.5l3.25-3.25M3 13.25h10" /></svg>
    </a>
  </article>
{/if}

<style>
  .conversation-file-card { min-width: 0; }
  /* The person's things share the raised step; the card is a quiet row, not a document. */
  .cv-file {
    display: flex;
    align-items: center;
    gap: var(--cv-s3);
    /* As wide as its name needs, from 320 up to the message column, so a name only gives
       way when there is truly no room. */
    inline-size: max-content;
    min-inline-size: min(100%, 320px);
    max-inline-size: 100%;
    box-sizing: border-box;
    padding: 10px var(--cv-s2) 10px 10px;
    border-radius: var(--cv-r-lg);
    background: var(--cv-raised);
    font-family: var(--cv-font);
  }
  .cv-file-tile {
    flex: none;
    display: grid;
    place-items: center;
    inline-size: 36px;
    block-size: 36px;
    border-radius: var(--cv-r-md);
    background: var(--cv-control);
    color: var(--cv-t3);
  }
  .cv-file-tile svg, .cv-file-download svg {
    inline-size: 18px;
    block-size: 18px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.4;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .cv-file-download svg { inline-size: 16px; block-size: 16px; stroke-width: 1.6; }
  .cv-file-identity { flex: 1; min-width: 0; display: grid; }
  .cv-file-name {
    overflow: hidden;
    color: var(--cv-t1);
    font-size: var(--cv-type-ui);
    font-weight: var(--cv-weight-label);
    line-height: var(--cv-leading-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .cv-file-meta {
    color: var(--cv-t3);
    font-size: var(--cv-type-caption);
    line-height: var(--cv-leading-caption);
    font-variant-numeric: tabular-nums;
  }
  .cv-file-preview, .cv-file-download {
    flex: none;
    display: inline-grid;
    place-items: center;
    block-size: 32px;
    border-radius: var(--cv-r-md);
    color: var(--cv-t2);
    text-decoration: none;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .cv-file-preview {
    padding: 0 var(--cv-s3);
    font-size: var(--cv-type-meta);
    font-weight: var(--cv-weight-label);
    line-height: var(--cv-leading-meta);
  }
  .cv-file-download { inline-size: 32px; color: var(--cv-t3); }
  .cv-file-preview:hover, .cv-file-download:hover { background: var(--cv-control); color: var(--cv-t1); }
  .cv-file-preview:focus-visible, .cv-file-download:focus-visible {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: var(--cv-focus-offset);
  }
  @media (prefers-reduced-motion: reduce) { .cv-file-preview, .cv-file-download { transition: none; } }
</style>
