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
    /** The person's own message: its words sit in a bubble, and what they attached
     *  stands beside the bubble in the order it was sent, never nested inside it. */
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
    return result;
  });
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
      byteCount={"data" in piece ? base64DecodedByteCount(piece.data) : piece.byte_count}
    />
  {/if}
{/snippet}

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

<style>
  /* The person's words, and only their words, in the thread's one bubble. */
  .c2-bubble {
    min-width: 0;
    max-width: 100%;
    background: var(--surface-2);
    color: var(--text-strong);
    border-radius: var(--radius-lg);
    padding: var(--space-3) var(--space-4);
  }
</style>
