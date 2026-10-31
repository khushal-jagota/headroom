<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../lib/queryCatalogue";
  import type { InlineEditDraft } from "../lib/inlineEditDraft";
  import { readStoredPage } from "../lib/pageKit";
  import InlineEdit from "./InlineEdit.svelte";
  import MarkdownBlock from "./MarkdownBlock.svelte";
  import PageProposalBody from "./pageKit/PageProposalBody.svelte";
  import ResourceState from "./ResourceState.svelte";

  let { ticketId, name, value, editable, emptyText, onSave, draft }: {
    ticketId?: string;
    name: string;
    value?: string;
    editable: boolean;
    emptyText: string;
    onSave?: (raw: string) => Promise<unknown>;
    draft?: InlineEditDraft;
  } = $props();
  const field = createQuery(() => ({
    ...queries.workspaceTicketField(ticketId ?? "", name),
    enabled: ticketId !== undefined && value === undefined
  }));
  let body = $derived(value ?? field.data?.value);
</script>

<ResourceState shape="block" error={value === undefined ? field.error : null} loading={field.isLoading}
  hasData={body !== undefined} loadingText="Loading field...">
  {#if body !== undefined}
    {#if readStoredPage(body).intent !== "prose"}
      <PageProposalBody {body} editable={editable && !!onSave} {onSave} />
    {:else if editable && onSave}
      <div class="ticket-field-value">
        <InlineEdit value={body} {draft} markdown multiline placeholder="Value..." {onSave} />
      </div>
    {:else}
      <MarkdownBlock text={body} quiet={emptyText} />
    {/if}
  {/if}
</ResourceState>
