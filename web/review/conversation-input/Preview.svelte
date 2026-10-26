<script lang="ts">
  import ConversationPane from '../../src/components/conversation/ConversationPane.svelte';
  import ManagerFrame from '../../src/components/ManagerFrame.svelte';
  import ManagerHeader from '../../src/components/ManagerHeader.svelte';
  import ArtifactPreview from '../../src/components/ArtifactPreview.svelte';
  import { conversationDock } from '../../src/lib/conversation/layout/dock';
  import { createConversationDismissal } from '../../src/lib/conversation/dismissal';
  import type { ConversationState } from '../../src/lib/conversation/conversationState';
  import type { TranscriptRow } from '../../src/lib/conversation/transcript';
  import type { SentMessagePiece, PromptDeliveryMode } from '../../src/lib/conversation/wire';
  import type { RunValues } from '../../src/lib/conversation/composer';
  import { tick, onMount, untrack } from 'svelte';
  import { ConversationViewReader, type ViewReaderAnchor } from '../../src/lib/conversation/view/reader.svelte';
  import { copyViewReaderAnchor } from '../../src/lib/conversation/view/copyAnchor';
  import { installConversationViewReaderFixture } from '../../tests/support/conversationViewReaderFixture';
  import type { ManagedFileTarget } from '../../src/lib/filePreview';

  type Host = 'ticket' | 'manager' | 'chief' | 'artifact';
  type Activity = 'idle' | 'unread' | 'working';
  let host = $state<Host>('ticket');
  let activity = $state<Activity>('idle');
  let width = $state(736);
  function requestWidth() {
    window.parent.postMessage({type:'panels-conversation-review-width',width},'*');
  }
  let conversationState = $state<ConversationState>('rest');
  let lens = $state<'focus' | 'full'>('focus');
  type Position = 'normal' | 'restored' | 'timeout' | 'unavailable' | 'fetch-error';
  let position = $state<Position>('normal');
  let previewHost: HTMLDivElement;
  let positionOperation = 0;
  let scenarioGeneration = $state(0);
  let reader = $state.raw<ConversationViewReader | null>(null);
  let viewAnchor = $state<ViewReaderAnchor | null>(null);
  const currentAnchor = (): ViewReaderAnchor | null => viewAnchor;
  const currentReader = (): ConversationViewReader | null => reader;
  let capturedAnchor = $state<ViewReaderAnchor | null>(null);
  let capturedCharacter = $state<{text:string;character:number;offset:number} | null>(null);
  function readCharacter() {
    const thread = previewHost.querySelector<HTMLElement>('[data-conversation-thread]');
    if (!thread) return null;
    const viewport = thread.getBoundingClientRect();
    const walker = document.createTreeWalker(thread,NodeFilter.SHOW_TEXT);
    for (let node=walker.nextNode();node;node=walker.nextNode()) {
      if (!node.parentElement?.closest('.markdown-block') || !node.textContent?.trim()) continue;
      const range=document.createRange();
      range.selectNodeContents(node);
      if(!Array.from(range.getClientRects()).some(rect=>rect.bottom>viewport.top+24&&rect.top<viewport.bottom))continue;
      for(let character=0;character<node.textContent.length;character++) {
        range.setStart(node,character);range.setEnd(node,character+1);
        const rect=range.getBoundingClientRect();
        if(rect.width&&rect.height&&rect.top>=viewport.top+24&&rect.bottom<=viewport.bottom)
          return {text:node.textContent,character,offset:rect.top-viewport.top};
      }
    }
    return null;
  }
  let scenarioPhase = $state('initial');
  let fixtureReady = false;
  const probes = () => window as unknown as {
    __returnProbe: {hold: boolean; fail: boolean; bodies: boolean; bodyHold: boolean; holdTargetOnly: boolean;
      targetBodyWaiting: number; bodyWaiting:number; missingTarget: boolean; failLatest: boolean; changedSource: boolean; releaseBodies: () => void};
    __backlog: (count: number) => void;
  };
  function resetTransport() {
    if (!fixtureReady) return;
    const probe = probes().__returnProbe;
    probe.releaseBodies();
    Object.assign(probe, {hold:false,fail:false,bodies:false,bodyHold:false,holdTargetOnly:false,
      targetBodyWaiting:0,bodyWaiting:0,missingTarget:false,failLatest:false,changedSource:false});
  }
  function attach(anchor: ViewReaderAnchor | null) {
    reader?.close();
    viewAnchor = anchor;
    const next = new ConversationViewReader('local-input-preview',lens,true,()=>{},anchor);
    reader = next;
    void next.connect().catch(() => { scenarioPhase = 'transport-error'; });
  }
  $effect(() => {
    const selectedLens = lens;
    const current = untrack(() => reader);
    if (current && current.lens !== selectedLens) {
      untrack(() => { positionOperation++; attach(viewAnchor); });
    }
  });
  async function waitFor(predicate: () => boolean, operation: number) {
    const deadline = performance.now() + 12000;
    while (operation === positionOperation && !predicate()) {
      if (performance.now() > deadline) throw new Error('Preview setup timed out: '+scenarioPhase);
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    }
    return operation === positionOperation;
  }
  async function selectPosition() {
    const operation = ++positionOperation;
    reader?.close();
    reader = null;
    viewAnchor = null;
    scenarioGeneration++;
    conversationState = 'rest';
    await tick();
    if(operation !== positionOperation)return;
    resetTransport();
    // Start each scenario from the same finite text, then capture the actual
    // viewport anchor. Only setup scrolls. Recovery placement belongs to production.
    probes().__backlog(-((window as unknown as {__previewBacklog?: number}).__previewBacklog ?? 0));
    (window as unknown as {__previewBacklog?: number}).__previewBacklog = 0;
    capturedAnchor = null;
    capturedCharacter = null;
    scenarioPhase = 'prepare-native';
    conversationState = 'opened';
    attach(null);
    await tick();
    try {
      if (!await waitFor(() => {
        const target = previewHost?.querySelector('[data-conversation-view-key="e2000"] .markdown-host');
        return !!target && !target.hasAttribute('data-markdown-rendering') &&
          !!target.textContent?.includes('Late paragraph 2000.') &&
          previewHost.querySelector('.conversation-transcript-region')?.getAttribute('aria-busy') === 'false';
      },operation)) return;
      const thread = previewHost.querySelector<HTMLElement>('[data-conversation-thread]');
      if (!thread) throw new Error('Preview thread is absent');
      // This earlier position is scenario input, never its final outcome.
      if (position === 'normal') { scenarioPhase = 'ready'; return; }
      scenarioPhase = 'prepare-anchor';
      // Choose the saved INPUT inside this real row. End-layout growth cannot
      // change which row this position selects. Recovery never uses this placement.
      const targetRow = previewHost.querySelector<HTMLElement>('[data-conversation-view-key="e2000"]');
      if(!targetRow)throw new Error('The synthetic target row is absent');
      thread.dispatchEvent(new WheelEvent('wheel',{deltaY:-600,bubbles:true}));
      thread.scrollTop += targetRow.getBoundingClientRect().top-thread.getBoundingClientRect().top+200;
      thread.dispatchEvent(new Event('scroll'));
      if (!await waitFor(() => {
        // Text registration can settle after the first scroll event. Capture the
        // same stationary reading again without any further movement.
        thread.dispatchEvent(new Event('scroll'));
        return !!currentAnchor()?.content && !currentAnchor()?.following && currentAnchor()?.key==='e2000';
      },operation)) return;
      capturedAnchor = copyViewReaderAnchor(currentAnchor()!);
      scenarioPhase = 'prepare-character';
      capturedCharacter = readCharacter();
      currentReader()?.close();
      probes().__backlog(100);
      (window as unknown as {__previewBacklog?: number}).__previewBacklog = 100;
      Object.assign(probes().__returnProbe, {
        bodies:true, bodyHold:position === 'timeout' || position === 'restored', holdTargetOnly:true,
        missingTarget:position === 'unavailable' || position === 'fetch-error',
        failLatest:position === 'fetch-error'
      });
      scenarioPhase = 'restoration';
      attach(copyViewReaderAnchor(capturedAnchor));
    } catch (error) { scenarioPhase = 'setup-error'; console.error(error); }
  }
  function releaseBody() { probes().__returnProbe.releaseBodies(); }
  onMount(() => {
    installConversationViewReaderFixture('local-input-preview', {search:'?text-restoration=paragraphs',baseUrl:'https://preview.invalid'});
    fixtureReady = true;
    attach(null);
    scenarioPhase = 'ready';
    return () => { positionOperation++; reader?.close(); resetTransport(); };
  });
  let artifactOpen = $state(true);
  let artifactReload = $state(0);
  let sentRows = $state<TranscriptRow[]>([]);
  const artifactTarget: ManagedFileTarget = {kind:'ticket-file',ticketId:'t_preview',path:'sample.html'};
  const dismissal = createConversationDismissal(() => { conversationState = 'rest'; });
  const dismissOutside = dismissal.surface;
  const samples: TranscriptRow[] = [
    {key:'sample-prompt',kind:'prompt',sequence:1,createdAt:1700000000,senderLabel:'owner',mode:'queue',sentAtUnixMilliseconds:1700000000000,
      content:[{piece:'text',text:'Please preserve the selected conversation input.'}]},
    {key:'sample-reply',kind:'agent_message',sequence:2,createdAt:1700000010,toOwner:true,
      content:[{piece:'text',text:'The preview is ready for your review. You can type here, open the conversation, and return to the dock.'}]},
    {key:'sample-end',kind:'turn_ended',sequence:3,createdAt:1700000011,ending:'completed',errorSummary:null,automaticCompactionResult:null}
  ];
  let rows = $derived([...samples,...sentRows]);
  let running = $derived(activity === 'working');
  let ownerReadThroughSequence = $derived(activity === 'unread' ? 1 : 10000);
  let roleLabel = $derived(host === 'chief' ? 'Chief' as const : host === 'manager' ? 'Manager' as const : 'Worker' as const);
  let openedFile = $derived(host === 'artifact' && artifactOpen ? artifactTarget : null);

  async function send(content: SentMessagePiece[], mode: PromptDeliveryMode, _picked: RunValues): Promise<boolean> {
    (window as unknown as {__appendPreviewConversation:(text:string)=>void}).__appendPreviewConversation(content.filter(piece=>piece.piece==='text').map(piece=>piece.text).join('\n'));
    const sequence = 4 + sentRows.length;
    const now = Date.now();
    sentRows = [...sentRows,
      {key:'local-'+sequence,kind:'prompt',sequence,createdAt:now/1000,content:content.filter(piece => piece.piece === 'text'),senderLabel:'owner',mode,sentAtUnixMilliseconds:now},
      {key:'local-'+(sequence+1),kind:'agent_message',sequence:sequence+1,createdAt:now/1000+1,toOwner:true,
        content:[{piece:'text',text:'This is a local sample reply. Your message stays in this preview.'}]},
      {key:'local-'+(sequence+2),kind:'turn_ended',sequence:sequence+2,createdAt:now/1000+2,ending:'completed',errorSummary:null,automaticCompactionResult:null}
    ];
    activity = 'unread';
    return true;
  }
</script>

<div class="review-shell">
  <header class="review-controls">
    <div class="review-heading"><strong>Conversation input</strong><span>Synthetic conversations · no messages leave this preview</span></div>
    <div class="review-options">
      <label>Host <select aria-label="Host" bind:value={host} onchange={() => { artifactOpen = true; }}><option value="ticket">Ticket</option><option value="manager">Manager</option><option value="chief">Chief</option><option value="artifact">Ticket with artifact</option></select></label>
      <label>State <select aria-label="State" bind:value={activity}><option value="idle">Idle</option><option value="unread">Unread reply</option><option value="working">Working</option></select></label>
      <label>Saved position <select aria-label="Saved position" bind:value={position} onchange={selectPosition}><option value="normal">Normal conversation</option><option value="restored">Successful saved text · held body</option><option value="timeout">Five-second timeout · held body</option><option value="unavailable">Unavailable saved target</option><option value="fetch-error">Newest fetch error</option></select></label>
      <button type="button" onclick={releaseBody}>Release saved body</button>
      <label>Viewport width <select aria-label="Host width" bind:value={width} onchange={requestWidth}><option value={736}>Screenshot · 736 px</option><option value={1250}>Wide · 1250 px</option><option value={390}>Mobile · 390 px</option></select></label>
      <button type="button" onclick={() => { conversationState = conversationState === 'rest' ? 'opened' : 'rest'; }}>{conversationState === 'rest' ? 'Open conversation' : 'Return to dock'}</button>
      {#if host === 'artifact' && !artifactOpen}<button type="button" onclick={() => { artifactOpen = true; }}>Open sample artifact</button>{/if}
    </div>
    <p class="review-simulation" data-preview-phase={scenarioPhase}>Local transport · {scenarioPhase} · reader {reader?.restorationOutcome ?? 'idle'}{reader?.trouble ? ' · message fetch error' : ''}</p>
    <output hidden data-preview-character={capturedCharacter ? JSON.stringify(capturedCharacter) : ''} data-preview-current-anchor={viewAnchor ? JSON.stringify(viewAnchor) : ''} data-preview-anchor={capturedAnchor ? JSON.stringify(capturedAnchor) : ''}></output>
  </header>
  <div class="review-stage">
    <div class="review-host" bind:this={previewHost} style:width={`${width}px`} data-review-host={host} data-review-width={width}>
      {#snippet pane()}
        {#key scenarioGeneration}
        <ConversationPane conversationId="local-input-preview" draftKey="review:conversation-input"
          conversationExists bind:conversationState bind:lens {reader} bind:viewAnchor {rows} {running} {ownerReadThroughSequence} {roleLabel}
          connectionTrouble={reader?.trouble ?? false} backendKey="codex" current={{model:'gpt-6.1-sol',reasoningEffort:'medium'}}
          models={[{model_id:'gpt-6.1-sol',display_name:'GPT-6.1',enabled:true}]} effortOptions={['medium']}
          ownSenderLabel="owner" onSend={send} onStop={() => { activity = 'idle'; }} />
        {/key}
      {/snippet}
      {#if host === 'ticket' || host === 'artifact'}
        <div class="ticket-screen" data-screen="ticket">
          <div class="ticket-page" use:conversationDock use:dismissOutside>
            <div class="ticket-reading">
              <main class="ticket-doc"><div class="ticket-head"><div class="page-eyebrow">Panels · Ticket</div><h1 class="ticket-title">Restore the approved conversation input</h1></div>
                <div class="ticket-col review-prose"><p>This sample Ticket keeps the conversation beside the work.</p><h2>Review the input</h2><p>Type a draft and add several lines. Open the conversation, then return to the dock. Your draft stays in place.</p><p>The unread and working states are local examples.</p><div class="review-spacer"></div><p>The document scrolls above the dock.</p></div>
              </main>
              {#if openedFile}<ArtifactPreview target={openedFile} reloadSignal={artifactReload} registerArtifactFrame={dismissal.registerArtifactFrame} onRefresh={() => { artifactReload += 1; }} onClose={() => { artifactOpen = false; }} />{/if}
            </div>
            <section class="conversation-layer"><div class="conversation-column">{@render pane()}</div></section>
          </div>
        </div>
      {:else}
        <ManagerFrame class={host === 'chief' ? 'chief-workspace' : ''} bind:conversationState>
          <ManagerHeader>{#snippet title()}{host === 'chief' ? 'Chief of Staff' : 'Conversation design'}{/snippet}
            {#snippet eyebrow()}<div class="page-eyebrow">Panels · {host === 'chief' ? 'Chief' : 'Manager'}</div>{/snippet}
          </ManagerHeader>
          <section class="manager-workers review-prose" aria-label="Workers"><p>{host === 'chief' ? 'A sample workspace for the Chief.' : 'A sample workspace for the Manager.'}</p><h2>Restore the approved conversation input</h2><p>Type a draft. Compare the rounded input at each width and state.</p><div class="review-spacer"></div><p>The document scrolls above the dock.</p></section>
          {#snippet conversation()}{@render pane()}{/snippet}
        </ManagerFrame>
      {/if}
    </div>
  </div>
  <footer class="review-footer">Revision __PREVIEW_REVISION__ · Width is capped by the available preview space.</footer>
</div>

<style>
  :global(html), :global(body), :global(#app) { margin:0; height:100%; min-width:0; }
  .review-shell { height:100%; display:flex; flex-direction:column; background:var(--surface-recessed); color:var(--text-default); font-family:var(--font-ui); }
  .review-controls { flex:none; padding:12px 16px; border-bottom:1px solid var(--border-color); background:var(--surface-1); }
  .review-heading { display:flex; gap:8px 20px; flex-wrap:wrap; align-items:baseline; font-size:15px; }
  .review-heading span { color:var(--text-muted); font-size:13px; }
  .review-options { display:flex; flex-wrap:wrap; gap:8px 16px; margin-top:10px; align-items:center; }
  .review-options label { display:flex; gap:8px; align-items:center; color:var(--text-muted); font-size:13px; }
  .review-options select, .review-options button { background:var(--surface-2); color:var(--text-default); border:1px solid var(--border-color); border-radius:6px; padding:7px 9px; font:inherit; }
  .review-options button { cursor:pointer; font-size:13px; }
  .review-simulation { font-size:13px; line-height:1.4; color:var(--text-muted); margin:8px 0 0; }
  .review-stage { display:flex; flex:1; min-height:0; justify-content:center; overflow:hidden; }
  .review-host { max-width:100%; height:100%; min-width:0; background:var(--surface-1); }
  .review-host > :global(.ticket-screen) { height:100%; }
  .review-prose { font-size:15px; line-height:1.6; }
  .review-prose h2 { font-size:18px; margin-top:28px; }
  .review-spacer { height:550px; }
  .review-footer { flex:none; font-size:11px; padding:5px 12px; color:var(--text-faint); text-align:center; }
</style>
