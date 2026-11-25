<script lang="ts">
  /** A message drawn as the pieces it is made of.
   *
   * Words go through the markdown renderer, whoever wrote them. That is the whole of
   * this decision and it is the same one for both sides of the thread: a link you paste
   * into your own message becomes the same preview the agent's links become, instead of
   * sitting there as literal text — which is the one thing in the thread that used to be
   * treated differently, for no reason anybody could name.
   *
   * A picture is drawn as the picture it is, and opens the expanded view. Pictures and
   * videos side by side form a grid.
   */
  import { getContext, type Snippet } from 'svelte';
  import { conversationTextPresentations, TextPresentationRegistry } from '../../lib/conversation/view/textPresentation';
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import VisibleMessageImage from "./VisibleMessageImage.svelte";
  import ConversationFileCard from "./ConversationFileCard.svelte";
  import LocalMediaTile from "./LocalMediaTile.svelte";
  import type { LocalMediaPiece } from "../../lib/conversation/localSentMedia";
  import { mediaSizeOf } from "../../lib/mediaFrame";
  import { base64DecodedByteCount } from "../../lib/conversation/pendingFiles";
  import {
    conversationFileHref,
    type MessagePiece,
    type SentMessagePiece
  } from "../../lib/conversation/wire";

  let {
    content,
    conversationId,
    presentationRowKey = null,
    bubble = false,
    failed = false,
    footer,
    backdrops
  }: {
    presentationRowKey?: string | null;
    /** The person's own message: one bubble. v8 (6): what they attached sits inside it,
     *  above the words, in the order it was sent. */
    bubble?: boolean;
    /** The message was not delivered: the bubble wears the failure outline (B5). */
    failed?: boolean;
    /** What the bubble says under its words: "Not delivered · …" and its actions (B5). */
    footer?: Snippet;
    content: readonly Piece[];
    /** The browser's own copy of a picture or video this browser sent, by the piece's
     *  place in `content`. It sits under the stored one until that has loaded. */
    backdrops?: ReadonlyMap<number, string>;
    /** Which conversation's files these pieces name. A file is fetched under the
     *  conversation that kept it, so a piece can never reach another one's. */
    conversationId: string;
  } = $props();
  type Piece = MessagePiece | SentMessagePiece | LocalMediaPiece;
  const isMediaFile = (piece: Piece) =>
    piece.piece === "image" || piece.piece === "local_media" || (piece.piece === "file" && /^(image|video)\//.test(piece.media_type));
  // Two or more pictures or videos sent side by side are laid out together as a grid.
  // In a bubble, words written one after another share one bubble.
  let runs = $derived.by(() => {
    const result: { at: number; pieces: { piece: Piece; at: number }[] }[] = [];
    content.forEach((piece, at) => {
      const last = result[result.length - 1];
      if (last && isMediaFile(piece) && isMediaFile(last.pieces[0].piece)) last.pieces.push({ piece, at });
      else if (last && bubble && piece.piece === "text" && last.pieces[0].piece.piece === "text") last.pieces.push({ piece, at });
      else result.push({ at, pieces: [{ piece, at }] });
    });
    // The person's message reads attachments first, then the words about them (v8: both in
    // the one bubble). Each keeps the order it was sent in. An agent's message keeps its
    // pieces where they were written.
    if (!bubble) return result;
    const isWords = (run: (typeof result)[number]) => run.pieces[0].piece.piece === "text";
    return [...result.filter(run => !isWords(run)), ...result.filter(isWords)];
  });
  const isWordsRun = (run: (typeof runs)[number]) => run.pieces[0].piece.piece === "text";
  /** v8 (6): a bubble with attachments holds them above its words. */
  let attachedRuns = $derived(bubble ? runs.filter(run => !isWordsRun(run)) : []);
  let wordRuns = $derived(bubble ? runs.filter(isWordsRun) : []);
  let lastWordsAt = $derived(wordRuns.at(-1)?.at ?? null);
  /** v9 (19): a bubble whose one attachment is one image is as wide as that image (min 240);
   *  its words wrap to it. */
  let oneImage = $derived(attachedRuns.length === 1 && attachedRuns[0].pieces.length === 1 && isOneImage(attachedRuns[0].pieces[0].piece));
  /** The sent picture and the recorded one make the same bubble. */
  function isOneImage(piece: Piece): boolean {
    return piece.piece === "image" || (piece.piece === "local_media" && piece.kind === "image");
  }
  const presentations = getContext<TextPresentationRegistry | undefined>(conversationTextPresentations);
</script>

{#snippet drawn(piece: Piece, at: number)}
  {#if piece.piece === "text"}
    <MarkdownBlock text={piece.text} registerPresentation={presentationRowKey !== null && presentations ? (host, presentation) => presentations.register(presentationRowKey!, at, host, presentation) : undefined} />
  {:else if piece.piece === "image"}
    <VisibleMessageImage
      outgoing={"data" in piece}
      src={() => "data" in piece
        ? `data:${piece.media_type};base64,${piece.data}`
        : conversationFileHref(conversationId, piece.stored_file_id)}
      alt={piece.file_name ?? "an image in this message"}
      size={"data" in piece ? null : mediaSizeOf(piece)}
      backdrop={backdrops?.get(at)}
    />
  {:else if piece.piece === "local_media"}
    <LocalMediaTile media={piece} />
  {:else if piece.piece === "attachment_upload" || piece.piece === "video_upload"}
    <span class="message-attachment-reference" data-message-upload-reference>Attached file</span>
  {:else if piece.piece === "file"}
    <ConversationFileCard
      href={"data" in piece
        ? `data:${piece.media_type};base64,${piece.data}`
        : conversationFileHref(conversationId, piece.stored_file_id)}
      fileName={piece.file_name}
      mediaType={piece.media_type}
      byteCount={"data" in piece ? base64DecodedByteCount(piece.data) : piece.byte_count}
      mediaSize={"data" in piece ? null : mediaSizeOf(piece)}
      backdrop={backdrops?.get(at)}
    />
  {/if}
{/snippet}

{#snippet attachment(run: (typeof runs)[number])}
  {#if run.pieces.length > 1}
    <div class="file-preview-grid">
      {#each run.pieces as entry (entry.at)}{@render drawn(entry.piece, entry.at)}{/each}
    </div>
  {:else}
    {@render drawn(run.pieces[0].piece, run.at)}
  {/if}
{/snippet}

{#if bubble && attachedRuns.length > 0}
  <!-- v8 (6): attachments belong to their message: inside the bubble, above its words, 8px
       apart; the bubble is 8px around them and 12 16 around the words. -->
  <div class="c2-bubble c2-bubble--attached" class:is-failed={failed} class:c2-bubble--one-image={oneImage} data-conversation-bubble data-conversation-bubble-attached>
    <div class="c2-bubble-attachments">
      {#each attachedRuns as run (run.at)}{@render attachment(run)}{/each}
    </div>
    {#each wordRuns as run (run.at)}
      <div class="c2-bubble-words">
        {#each run.pieces as entry (entry.at)}{@render drawn(entry.piece, entry.at)}{/each}
      </div>
    {/each}
    {#if footer}<div class="c2-bubble-words">{@render footer()}</div>{/if}
  </div>
{:else}
{#each runs as run (run.at)}
  {#if bubble && run.pieces[0].piece.piece === "text"}
    <div class="c2-bubble" class:is-failed={failed} data-conversation-bubble>
      {#each run.pieces as entry (entry.at)}{@render drawn(entry.piece, entry.at)}{/each}
      {#if footer && run.at === lastWordsAt}{@render footer()}{/if}
    </div>
  {:else if run.pieces.length > 1}
    <div class="file-preview-grid">
      {#each run.pieces as entry (entry.at)}{@render drawn(entry.piece, entry.at)}{/each}
    </div>
  {:else}
    {@render drawn(run.pieces[0].piece, run.at)}
  {/if}
{/each}
{#if bubble && footer && runs.length === 0}
  <div class="c2-bubble" class:is-failed={failed} data-conversation-bubble>{@render footer()}</div>
{/if}
{/if}

<style>
  /* The person's words, and only their words, in the thread's one bubble: no edge, radius 18,
     padding 12 16, in the ink. */
  .c2-bubble {
    min-width: 0;
    max-width: 100%;
    background: var(--cv-bubble);
    color: var(--cv-ink);
    border-radius: 18px;
    padding: 12px 16px;
    font-family: var(--cv-font);
    font-size: var(--cv-type-prose);
    line-height: var(--cv-leading-prose);
    overflow-wrap: anywhere;
  }
  .c2-bubble :global(.markdown) { color: inherit; }
  /* A message that is not delivered keeps its bubble and wears a 1px failure outline, drawn
     inside the edge so the bubble does not move. */
  .c2-bubble.is-failed { outline: 1px solid rgb(255 143 133 / 55%); outline-offset: -1px; }
  /* With attachments, the bubble holds them above its words: padding 8 around the attachments,
     12 16 around the words (8 + 4 8). Inside, the attachments' corners follow the bubble's
     (18 - 8 = 10). */
  .c2-bubble--attached { display: flex; flex-direction: column; gap: 8px; padding: 8px; }
  .c2-bubble-attachments { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; min-width: 0; }
  .c2-bubble-words { padding: 4px 8px; min-width: 0; }
  .c2-bubble.c2-bubble--attached :global(.c2-piece-image-slot) { display: flex; justify-content: flex-start; max-width: 100%; margin: 0; }
  .c2-bubble.c2-bubble--attached :global(.file-preview-media) { border-radius: 10px; }
  .c2-bubble.c2-bubble--attached :global(.conversation-file-card) { margin: 0; border-radius: 10px; }
  /* File cards stretch to the bubble's inner width. */
  .c2-bubble-attachments > :global(.conversation-file-card) { align-self: stretch; width: auto; max-width: none; }
  /* One image: the bubble takes the image's width (at least 240); the words take no part in
     its width (0 wide, at least the full line), so they wrap to it. */
  .c2-bubble--one-image { min-width: 240px; }
  .c2-bubble--one-image > .c2-bubble-words { width: 0; min-width: 100%; }
  /* The bubble is as wide as its picture. A percentage cap counts as no cap when the
     bubble measures its content, so a wide picture capped at 200 high asked for its 200-high
     width (wider than the 320 it is drawn at) and the bubble stood wider than the picture. The
     cap in pixels makes the measured width the drawn one; the picture fills the content width
     and the bubble's 8px padding is equal on every side. */
  .c2-bubble--one-image :global(.file-preview-media),
  .c2-bubble--one-image :global(.file-preview-media > :is(.file-preview-image, .file-preview-video-poster)) { max-width: 320px; }
</style>
