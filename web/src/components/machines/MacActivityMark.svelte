<script lang="ts">
  import { createQuery } from "@tanstack/svelte-query";
  import { queries } from "../../lib/queryCatalogue";
  import { macActivityKnown, type TicketLocale } from "../../lib/ticketMachines";
  import type { ConversationSignal } from "../../lib/conversationSignal";
  import ConversationSignalMark from "../ConversationSignalMark.svelte";
  let {locale,signal,conversationId=null,completed=false}: {locale?:TicketLocale;signal?:ConversationSignal;conversationId?:string|null;completed?:boolean}=$props();
  const health=createQuery(() => ({...queries.executorHealth(),enabled:locale === "macos",refetchInterval:false}));
  let unknown=$derived(locale === "macos" && (health.isError || !macActivityKnown(health.data)));
  let savedSignal=$derived(signal ? {...signal,working:signal.working && !unknown && !completed} : undefined);
</script>
<ConversationSignalMark signal={savedSignal} {conversationId} />


