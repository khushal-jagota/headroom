<script lang="ts">
  import type { TicketLocale } from "../../lib/ticketMachines";
  import type { ConversationSignal } from "../../lib/conversationSignal";
  import ConversationSignalMark from "../ConversationSignalMark.svelte";
  import MacActivityMark from "./MacActivityMark.svelte";
  // `working={false}`: a page title shows the unread dot but never the working spinner; the
  // status line over the composer says Working (owner).
  let {locale,signal,conversationId=null,completed=false,working=true}: {locale?:TicketLocale;signal?:Partial<ConversationSignal>;conversationId?:string|null;completed?:boolean;working?:boolean}=$props();
  let savedSignal = $derived(signal ? {...signal, working: signal.working && !completed && working} : undefined);
</script>
{#if locale === "macos"}
  <MacActivityMark {locale} {signal} {conversationId} {completed} {working} />
{:else}
  <span style="display:contents" data-machine-activity-unknown={completed ? "true" : undefined}><ConversationSignalMark signal={savedSignal} {conversationId} /></span>
{/if}
