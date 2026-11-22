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
  import { getContext } from 'svelte';
  import { conversationTextPresentations, TextPresentationRegistry } from '../../lib/conversation/view/textPresentation';
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import VisibleMessageImage from "./VisibleMessageImage.svelte";
  import ConversationFileCard from "./ConversationFileCard.svelte";
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
    bubble = false
  }: {
    presentationRowKey?: string | null;
    /** The person's own message: one bubble. v8 (6): what they attached sits inside it,
     *  above the words, in the order it was sent. */
    bubble?: boolean;
    content: readonly (MessagePiece | SentMessagePiece)[];
    /** Which conversation's files these pieces name. A file is fetched under the
     *  conversation that kept it, so a piece can never reach another one's. */
    conversationId: string;
  } = $props();
  const isMediaFile = (piece: MessagePiece | SentMessagePiece) =>
    piece.piece === "image" || (piece.piece === "file" && /^(image|video)\//.test(piece.media_type));
  // Two or more pictures or videos sent side by side are laid out together as a grid.
  // In a bubble, words written one after another share one bubble.
  let runs = $derived.by(() => {
    const result: { at: number; pieces: { piece: MessagePiece | SentMessagePiece; at: number }[] }[] = [];
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
  /** v9 (19): a bubble whose one attachment is one image is as wide as that image (min 240);
   *  its words wrap to it. */
  let oneImage = $derived(attachedRuns.length === 1 && attachedRuns[0].pieces.length === 1 && attachedRuns[0].pieces[0].piece.piece === "image");
  const presentations = getContext<TextPresentationRegistry | undefined>(conversationTextPresentations);
</script>

{#snippet drawn(piece: MessagePiece | SentMessagePiece, at: number)}
  {#if piece.piece === "text"}
    <MarkdownBlock text={piece.text} registerPresentation={presentationRowKey !== null && presentations ? (host, presentation) => presentations.register(presentationRowKey!, at, host, presentation) : undefined} />
  {:else if piece.piece === "image"}
    <VisibleMessageImage
      outgoing={"data" in piece}
      src={() => "data" in piece
        ? `data:${piece.media_type};base64,${piece.data}`
        : conversationFileHref(conversationId, piece.stored_file_id)}
      alt={piece.file_name ?? "an image in this message"}
    />
  {:else if piece.piece === "file"}
    <ConversationFileCard
      href={"data" in piece
        ? `data:${piece.media_type};base64,${piece.data}`
        : conversationFileHref(conversationId, piece.stored_file_id)}
      fileName={piece.file_name}
      mediaType={piece.media_type}
      byteCount={"data" in piece ? base64DecodedByteCount(piece.data) : piece.byte_count}
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
  <div class="c2-bubble c2-bubble--attached" class:c2-bubble--one-image={oneImage} data-conversation-bubble data-conversation-bubble-attached>
    <div class="c2-bubble-attachments">
      {#each attachedRuns as run (run.at)}{@render attachment(run)}{/each}
    </div>
    {#each wordRuns as run (run.at)}
      <div class="c2-bubble-words">
        {#each run.pieces as entry (entry.at)}{@render drawn(entry.piece, entry.at)}{/each}
      </div>
    {/each}
  </div>
{:else}
{#each runs as run (run.at)}
  {#if bubble && run.pieces[0].piece.piece === "text"}
    <div class="c2-bubble" data-conversation-bubble>
      {#each run.pieces as entry (entry.at)}{@render drawn(entry.piece, entry.at)}{/each}
    </div>
  {:else if run.pieces.length > 1}
    <div class="file-preview-grid">
      {#each run.pieces as entry (entry.at)}{@render drawn(entry.piece, entry.at)}{/each}
    </div>
  {:else}
    {@render drawn(run.pieces[0].piece, run.at)}
  {/if}
{/each}
{/if}

<style>
  /* The person's words, and only their words, in the thread's one bubble: the raised step,
     the first tone, no edge (design v3 §3.2). */
  .c2-bubble {
    min-width: 0;
    max-width: 100%;
    /* v7: user bubbles #242424, no border, radius 18, padding 12 16, 15/24 #ececec. */
    background: #242424;
    color: #ececec;
    border-radius: 18px;
    padding: 12px 16px;
    font-family: var(--cv-font);
    font-size: var(--cv-type-prose);
    line-height: var(--cv-leading-prose);
    overflow-wrap: anywhere;
  }
  .c2-bubble :global(.markdown) { color: inherit; }
  /* v8 (6): padding 8 around the attachments, 12 16 around the words (8 + 4 8). Inside, the
     attachments' corners follow the bubble's (18 - 8 = 10). */
  .c2-bubble--attached { display: flex; flex-direction: column; gap: 8px; padding: 8px; }
  .c2-bubble-attachments { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; min-width: 0; }
  .c2-bubble-words { padding: 4px 8px; min-width: 0; }
  .c2-bubble.c2-bubble--attached :global(.c2-piece-image-slot) { display: flex; justify-content: flex-start; max-width: 100%; margin: 0; }
  .c2-bubble.c2-bubble--attached :global(.file-preview-media) { border-radius: 10px; }
  .c2-bubble.c2-bubble--attached :global(.conversation-file-card) { margin: 0; border-radius: 10px; }
  /* v9 (19): file cards stretch to the bubble's inner width. */
  .c2-bubble-attachments > :global(.conversation-file-card) { align-self: stretch; width: auto; max-width: none; }
  /* v9 (19): one image: the bubble takes the image's width (at least 240); the words take no
     part in its width (0 wide, at least the full line), so they wrap to it. */
  .c2-bubble--one-image { min-width: 240px; }
  .c2-bubble--one-image > .c2-bubble-words { width: 0; min-width: 100%; }
</style>
