<script lang="ts">
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import MarkdownBlock from '../../MarkdownBlock.svelte';
  let { reader }: {
      reader: ConversationViewReader;
  } = $props();
  let text = $state('');
  let loading = false;
  let dirty = false;
  let generation = 0;
  let abort: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let lastReference = '';
  async function update(): Promise<void> {
      if (loading) {
          dirty = true;
          return;
      }
      const reference = reader.status?.live_output?.reference;
      if (!reference) {
          text = '';
          return;
      }
      const key = JSON.stringify(reference);
      if (key === lastReference)
          return;
      const current = ++generation;
      loading = true;
      dirty = false;
      abort = new AbortController();
      try {
          const value = await reader.body(reference, abort.signal);
          if (generation === current) {
              text = value;
              lastReference = key;
          }
      }
      catch { /* A replaced/settled spool is obsolete; the durable row owns the words. */ }
      finally {
          if (generation === current) {
              loading = false;
              if (dirty)
                  timer = setTimeout(() => { timer = null; void update(); }, 100);
          }
      }
  }
  $effect(() => {
      const output = reader.status?.live_output;
      if (output === null || output === undefined) {
          generation++;
          abort?.abort();
          loading = false;
          dirty = false;
          text = '';
          lastReference = '';
          return;
      }
      void output.version;
      if (timer !== null)
          return;
      timer = setTimeout(() => { timer = null; void update(); }, 100);
  });
  $effect(() => () => { generation++; abort?.abort(); if (timer !== null)
      clearTimeout(timer); text = ''; });
</script>
{#if text}<article class="chat-a c2-streaming" data-conversation-row="streaming"><MarkdownBlock {text}/></article>{/if}
<style>.c2-streaming { color: var(--text-muted); }</style>
