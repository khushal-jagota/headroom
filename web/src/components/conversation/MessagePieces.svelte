<script lang="ts">
  /** A message drawn as the pieces it is made of.
   *
   * Words go through the markdown renderer, whoever wrote them. That is the whole of
   * this decision and it is the same one for both sides of the thread: a link you paste
   * into your own message becomes the same preview the agent's links become, instead of
   * sitting there as literal text — which is the one thing in the thread that used to be
   * treated differently, for no reason anybody could name.
   *
   * A picture is drawn as the picture it is. That is the honest minimum; what it is not
   * yet is designed — thumbnails, a lightbox — and that is its own piece of work rather
   * than something to guess at here.
   */
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import { conversationFileHref, type MessagePiece } from "../../lib/conversation/wire";

  let {
    content,
    conversationId
  }: {
    content: readonly MessagePiece[];
    /** Which conversation's files these pieces name. A file is fetched under the
     *  conversation that kept it, so a piece can never reach another one's. */
    conversationId: string;
  } = $props();
</script>

{#each content as piece, at (at)}
  {#if piece.piece === "text"}
    <MarkdownBlock text={piece.text} />
  {:else if piece.piece === "image"}
    <img
      class="c2-piece-image"
      data-conversation-piece="image"
      src={conversationFileHref(conversationId, piece.stored_file_id)}
      alt={piece.file_name ?? "an image in this message"}
    />
  {/if}
{/each}

<style>
  .c2-piece-image {
    display: block;
    max-width: 100%;
    height: auto;
    margin-block: var(--space-2);
    border-radius: var(--radius-sm);
  }
</style>
