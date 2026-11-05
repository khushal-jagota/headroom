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
  let saveText: ((raw: string) => Promise<unknown>) | undefined;

  function start(): void {
    if (authorText) {
      const writer = authorText.begin(blockId), apply = update;
      saveText = raw => writer(block => apply(block, raw));
    }
  }

  async function save(raw: string): Promise<unknown> {
    if (!saveText) throw new Error("Start this text edit before saving it.");
    return saveText(raw);
  }
</script>

{#if authorText}
  <InlineEdit {value} {markdown} {multiline} {className} ariaLabel={label} placeholder={label}
    onStart={start} onSave={save} onCancel={() => { saveText = undefined; return value; }} />
{:else if markdown}
  <div class={className}><MarkdownBlock text={value} quiet="" /></div>
{:else}
  <span class={className}>{value}</span>
{/if}
