<script lang="ts">
  import type { TicketLocale } from "../../lib/ticketMachines";
  import type { ConversationSignal } from "../../lib/conversationSignal";
  import type { AgentHoldFacts } from "../../lib/workAttentionPresentation";
  import ConversationSignalMark from "../ConversationSignalMark.svelte";
  import MacActivityMark from "./MacActivityMark.svelte";
  // `working={false}`: a page title shows the unread dot but never the working spinner; the
  // status line over the composer says Working (owner).
  // `agentHold`: whether an agent holds the Ticket, which a Mac away turns into "Waiting for the Mac".
  let {locale,signal,conversationId=null,completed=false,working=true,agentHold}: {locale?:TicketLocale;signal?:Partial<ConversationSignal>;conversationId?:string|null;completed?:boolean;working?:boolean;agentHold?:AgentHoldFacts}=$props();
  let savedSignal = $derived(signal ? {...signal, working: signal.working && !completed && working} : undefined);
</script>
{#if locale === "macos"}
  <MacActivityMark {locale} {signal} {conversationId} {completed} {working} {agentHold} />
{:else}
  <span style="display:contents" data-machine-activity-unknown={completed ? "true" : undefined}><ConversationSignalMark signal={savedSignal} {conversationId} /></span>
{/if}
