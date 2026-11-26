<script lang="ts">
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import { DiagnosticExpiredError, DIAGNOSTIC_EXPIRED_MESSAGE } from "../../../lib/conversation/diagnosticExpiry";
  import MarkdownBlock from '../../MarkdownBlock.svelte';
  import { untrack } from 'svelte';
  let { reader }: {
      reader: ConversationViewReader;
  } = $props();
  let text = $state('');
  let arriving = $state(false);
  let expired = $state(false);
  /** The first text of a live reply is still on its way. The open waits for it. */
  let firstTextPending = $state(false);
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
          firstTextPending = false;
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
              expired = false;
              arriving = reader.liveOutputIsArrival(reference, lastReference !== "");
              text = value;
              lastReference = key;
          }
      }
      catch (reason) {
          if (generation === current && reason instanceof DiagnosticExpiredError) {
              text = "";
              expired = true;
          }
      }
      finally {
          if (generation === current) {
              loading = false;
              firstTextPending = false;
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
          expired = false;
          lastReference = '';
          firstTextPending = false;
          return;
      }
      void output.version;
      if (timer !== null)
          return;
      // The first text is read at once. Only later versions wait, so a fast stream coalesces.
      if (lastReference === '' && !loading) {
          firstTextPending = true;
          untrack(() => void update());
          return;
      }
      timer = setTimeout(() => { timer = null; void update(); }, 100);
  });
  $effect(() => () => { generation++; abort?.abort(); if (timer !== null)
      clearTimeout(timer); text = ''; });
</script>
{#if expired}<p class="c2-live-expired" role="status">{DIAGNOSTIC_EXPIRED_MESSAGE}</p>{/if}
{#if firstTextPending && !text}<span class="c2-live-pending" aria-busy="true" data-conversation-live-output-pending></span>{/if}
{#if text}<article class="chat-a c2-streaming" data-conversation-row="streaming" data-conversation-stream-arrival={arriving ? "true" : "false"}><MarkdownBlock {text}/></article>{/if}
<style>.c2-live-pending { display: none; }
.c2-live-expired { margin: 0; color: var(--cv-t3); font-family: var(--cv-font); font-size: var(--cv-type-meta); line-height: var(--cv-leading-meta); }</style>
