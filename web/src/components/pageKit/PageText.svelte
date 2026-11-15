<script lang="ts">
  import { getContext } from "svelte";
  import type { Block } from "../../lib/pageKit";
  import { PAGE_CONTEXT, type PageContext } from "../../lib/pageKit/context";
  import InlineEdit from "../InlineEdit.svelte";
  import MarkdownBlock from "../MarkdownBlock.svelte";

  let {
    blockId, field, value, label, update,
    markdown = false, multiline = false, className = ""
  }: {
    blockId: string;
    field: string;
    value: string;
    label: string;
    update: (block: Block, raw: string) => Block;
    markdown?: boolean;
    multiline?: boolean;
    className?: string;
  } = $props();

  const context = getContext<PageContext | undefined>(PAGE_CONTEXT);
  const authorText = $derived(context?.authorText);
  const draft = $derived(authorText?.draft(blockId, field));

  function start(): void {
    // A failed or remounted edit retains its original revision and writer.
    if (draft && draft.value === null && authorText) {
      draft.save = authorText.begin(blockId);
      draft.apply = update;
    }
  }

  async function save(raw: string): Promise<unknown> {
    const currentDraft = draft;
    const apply = currentDraft?.apply;
    if (!currentDraft?.save || !apply) throw new Error("This text edit no longer has its original save context.");
    const result = await currentDraft.save(block => apply(block, raw));
    currentDraft.save = undefined;
    currentDraft.apply = undefined;
    return result;
  }
</script>

{#if authorText && draft}
  <InlineEdit {value} {markdown} {multiline} {className} {draft} ariaLabel={label} placeholder={label}
    onStart={start} onSave={save} onCancel={() => { draft.save = undefined; draft.apply = undefined; return value; }} />
{:else if markdown}
  <div class={className}><MarkdownBlock text={value} quiet="" /></div>
{:else}
  <span class={className}>{value}</span>
{/if}
