<script lang="ts">
  import type { TicketLocale } from "../../lib/ticketMachines";
  import type { ConversationSignal } from "../../lib/conversationSignal";
  import ConversationSignalMark from "../ConversationSignalMark.svelte";
  import MacActivityMark from "./MacActivityMark.svelte";
  let {locale,signal,conversationId=null,completed=false}: {locale?:TicketLocale;signal?:ConversationSignal;conversationId?:string|null;completed?:boolean}=$props();
  let savedSignal = $derived(signal ? {...signal, working: signal.working && !completed} : undefined);
</script>
{#if locale === "macos"}
  <MacActivityMark {locale} {signal} {conversationId} {completed} />
{:else}
  <ConversationSignalMark signal={savedSignal} {conversationId} />
{/if}
