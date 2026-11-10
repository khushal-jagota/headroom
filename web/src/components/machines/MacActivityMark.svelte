<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import { macActivityKnown, macTicketActivityLabel, type TicketLocale } from "../../lib/ticketMachines";
  import type { ConversationSignal } from "../../lib/conversationSignal";
  import type { AgentHoldFacts } from "../../lib/workAttentionPresentation";
  import ConversationSignalMark from "../ConversationSignalMark.svelte";
  let {locale,signal,conversationId=null,completed=false,working=true,agentHold}: {locale?:TicketLocale;signal?:Partial<ConversationSignal>;conversationId?:string|null;completed?:boolean;working?:boolean;agentHold?:AgentHoldFacts}=$props();
  const health=createQuery(() => ({...queries.executorHealth(),enabled:locale === "macos",refetchInterval:false}));
  let unknown=$derived(locale === "macos" && (health.isError || !macActivityKnown(health.data)));
  let savedSignal=$derived(signal ? {...signal,working:signal.working && !unknown && !completed && working} : undefined);
  // The mark draws nothing new while the Mac is away; the reason is read out. The Ticket
  // page and the rail hover show it as text.
  let activityLabel=$derived(completed ? null : macTicketActivityLabel(health.isError ? undefined : health.data, {...agentHold, locale, conversation_signal: signal}));
</script>
<span style="display:contents" data-machine-activity-unknown={unknown || completed ? "true" : undefined}><ConversationSignalMark signal={savedSignal} {conversationId} />{#if activityLabel}<span class="mac-ticket-activity" data-mac-ticket-activity>{activityLabel}</span>{/if}</span>

<style>
  .mac-ticket-activity { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); white-space:nowrap; }
</style>
