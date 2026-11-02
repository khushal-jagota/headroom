<script lang="ts">
  import { getContext, onMount, tick, untrack } from 'svelte';
  import type { CredentialRequestRowBody } from '../../lib/conversation/view/contracts';
  import { CREDENTIAL_REQUEST_READ_ONLY, CREDENTIAL_REQUEST_DRAFTS, CredentialRequestDraft, type CredentialRequestDrafts } from '../../lib/conversation/credentialRequest.svelte';
  import { CredentialRequestWireError, mutateCredentialRequest } from '../../lib/conversation/wire';

  let { request }: { request: CredentialRequestRowBody } = $props();
  const paneReadOnly = getContext<(() => boolean) | undefined>(CREDENTIAL_REQUEST_READ_ONLY);
  const readOnly = $derived(paneReadOnly?.() ?? false);
  const uid = $props.id();
  const paneDrafts = getContext<(() => CredentialRequestDrafts) | undefined>(CREDENTIAL_REQUEST_DRAFTS);
  const draft = $derived.by(() => {
    const cache = paneDrafts?.();
    let value = cache?.get(request.request_id);
    if (!value) { value = new CredentialRequestDraft(request.name); cache?.set(request.request_id, value); }
    return value;
  });
  const current = $derived(draft.local ?? request);
  let root = $state<HTMLElement>();
  let nameInput = $state<HTMLInputElement>();
  let secretInput = $state<HTMLInputElement>();
  let result = $state<HTMLDivElement>();
  let reviewButton = $state<HTMLButtonElement>();
  let previousRequest = untrack(() => request);
  onMount(() => {
    const closeHelp = (event: KeyboardEvent): void => {
      if (event.key === 'Escape' && draft.helpOpen) {
        event.preventDefault(); event.stopPropagation(); draft.helpOpen = false;
      }
    };
    window.addEventListener('keydown', closeHelp, true);
    return () => window.removeEventListener('keydown', closeHelp, true);
  });
  $effect(() => {
    if (request.state !== 'pending') {
      draft.secret = ''; draft.revealed = false; draft.helpOpen = false;
      if (draft.local?.state === 'pending') draft.local = null;
    }
    if (request === previousRequest) return;
    if (request.request_id !== previousRequest.request_id) {
      draft.name = request.name; draft.secret = ''; draft.revealed = false; draft.helpOpen = false;
      draft.error = ''; draft.invalid = null; draft.stale = false; draft.saveFailed = false; draft.reviewed = false;
    }
    previousRequest = request;
    draft.local = null;
    if (request.state !== 'pending') {
      draft.secret = ''; draft.revealed = false; draft.helpOpen = false;
    }
  });
  const replacement = $derived(current.replacement !== null);
  const outcome = $derived(current.state === 'rejected' ? 'Rejected' : current.state === 'replaced' ? 'Replaced' : 'Saved');
  function ownsFocus(): boolean { return !!root?.contains(document.activeElement); }
  async function focusResult(): Promise<void> { await tick(); if (ownsFocus() || document.activeElement === document.body) result?.focus(); }
  function corrected(field: 'name' | 'secret', value: string): void {
    if (draft.invalid === field && value.trim()) { draft.invalid = null; draft.error = ''; }
    if (draft.saveFailed) { draft.saveFailed = false; draft.error = ''; }
  }
  async function invalidField(field: 'name' | 'secret', message: string): Promise<void> {
    draft.invalid = field; draft.error = message; draft.saveFailed = false;
    await tick(); (field === 'name' ? nameInput : secretInput)?.focus();
  }
  function inputKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); void submit(); }
  }
  async function submit(): Promise<void> {
    const submittingDraft = draft;
    if (readOnly || submittingDraft.busy || submittingDraft.stale || current.state !== 'pending') return;
    if (!submittingDraft.name.trim()) return invalidField('name', 'Enter a key name.');
    if (!submittingDraft.secret.trim()) return invalidField('secret', 'Paste the API key.');
    const ownedBefore = ownsFocus();
    submittingDraft.busy = true; submittingDraft.error = ''; submittingDraft.invalid = null; submittingDraft.saveFailed = false;
    try {
      const resolved = await mutateCredentialRequest(current.request_id, 'save', {
        name: replacement ? current.name : submittingDraft.name.trim(), value: submittingDraft.secret,
        expected_revision: current.replacement?.revision ?? null
      });
      const focus = ownsFocus() || ownedBefore && document.activeElement === document.body;
      submittingDraft.local = resolved; submittingDraft.secret = ''; submittingDraft.revealed = false; submittingDraft.helpOpen = false;
      if (focus) await focusResult();
    } catch (reason) {
      if (reason instanceof CredentialRequestWireError && reason.stale) {
        const focus = ownsFocus() || ownedBefore && document.activeElement === document.body;
        submittingDraft.secret = ''; submittingDraft.revealed = false; submittingDraft.stale = true; submittingDraft.busy = false;
        submittingDraft.error = 'This key changed. Review it before you replace it. Your replacement was not saved.';
        await tick(); if (focus) reviewButton?.focus();
      } else { submittingDraft.error = reason instanceof Error ? reason.message : 'Could not save. Try again.'; submittingDraft.saveFailed = true; }
    } finally { submittingDraft.busy = false; }
  }
  async function action(kind: 'reject' | 'review' | 'retry-delivery'): Promise<void> {
    const submittingDraft = draft;
    if (readOnly || submittingDraft.busy) return;
    const ownedBefore = ownsFocus();
    submittingDraft.busy = true; submittingDraft.error = ''; submittingDraft.invalid = null;
    try {
      const response = await mutateCredentialRequest(current.request_id, kind);
      const focus = ownsFocus() || ownedBefore && document.activeElement === document.body;
      submittingDraft.local = response; submittingDraft.busy = false;
      if (kind === 'review') {
        submittingDraft.secret = ''; submittingDraft.revealed = false; submittingDraft.stale = false; submittingDraft.saveFailed = false; submittingDraft.reviewed = true;
        await tick(); if (focus) secretInput?.focus();
      } else {
        submittingDraft.secret = ''; submittingDraft.revealed = false; submittingDraft.helpOpen = false;
        if (focus) await focusResult();
      }
    } catch (reason) { submittingDraft.error = reason instanceof Error ? reason.message : 'The request could not be completed.'; }
    finally { submittingDraft.busy = false; }
  }
</script>

<section bind:this={root} class="key-request" aria-label={`${current.provider} API key request`} data-conversation-row="credential_request" data-credential-request={current.request_id} data-credential-state={current.state}>
  {#if current.state !== 'pending'}
    <div bind:this={result} class="result-status" role="status" tabindex="-1">
      <div class="saved-row"><span class="saved-name">{current.credential?.reference.name ?? current.name}</span><span class="saved-status">{outcome}</span></div>
      {#if current.delivery?.status === 'queued'}<p class="state-note">Result queued for the agent.</p>
      {:else if current.delivery?.status === 'refused'}
        <div class="delivery-state"><p class="state-note">Result did not reach the agent.</p>
          {#if !readOnly}<button class="request-action ghost" type="button" disabled={draft.busy} onclick={() => void action('retry-delivery')}>{draft.busy ? 'Sending…' : 'Retry delivery'}</button>{/if}
        </div>
      {:else if current.delivery?.status === 'uncertain'}<p class="state-note">Delivery is uncertain. Check the conversation before another attempt.</p>
      {:else if current.delivery?.status === 'canceled'}<p class="state-note">Result delivery canceled.</p>{/if}
      {#if draft.error}<p class="error-note" role="alert">{draft.error}</p>{/if}
    </div>
  {:else if readOnly}
    <div class="saved-row"><span class="saved-name">{current.name}</span><span class="saved-status">Requested</span></div>
  {:else}
    <form autocomplete="off" aria-label="API key request" aria-describedby={draft.error && !draft.invalid ? `${uid}-error` : undefined}
      onsubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div class="stacked-fields">
        {#if replacement}<span class="fixed-name">{current.name}</span>
        {:else}<input bind:this={nameInput} bind:value={draft.name} class="key-name" aria-label="Key name" placeholder="Key name" autocomplete="off" disabled={draft.busy} aria-invalid={draft.invalid === 'name' ? 'true' : undefined} aria-describedby={draft.invalid === 'name' ? `${uid}-error` : undefined} onkeydown={inputKey} oninput={(event) => corrected('name', event.currentTarget.value)} />{/if}
        {#if !draft.stale}
          <div class="private-input"><input bind:this={secretInput} bind:value={draft.secret} type={draft.revealed ? 'text' : 'password'} aria-label={replacement ? 'New API key' : 'API key'} placeholder={replacement ? 'Paste new API key' : 'Paste API key'} autocomplete="off" spellcheck="false" disabled={draft.busy} aria-invalid={draft.invalid === 'secret' ? 'true' : undefined} aria-describedby={draft.invalid === 'secret' ? `${uid}-error` : undefined} onkeydown={inputKey} oninput={(event) => corrected('secret', event.currentTarget.value)} />
            {#if draft.secret}<button type="button" class="quiet-affordance reveal-button" aria-label={draft.revealed ? 'Hide API key' : 'Show API key'} aria-pressed={draft.revealed} disabled={draft.busy} onclick={() => draft.revealed = !draft.revealed}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>{#if draft.revealed}<path d="m3 3 18 18"/>{/if}</svg></button>{/if}
          </div>
        {/if}
      </div>
      <!-- Mutation labels describe work without marking the row as unfinished presentation. -->
      <div class="form-footer">
        {#if !draft.stale}<button type="button" class="request-action primary" disabled={draft.busy} onclick={() => void submit()}>{draft.busy ? 'Saving…' : draft.saveFailed ? 'Retry' : replacement ? 'Replace' : 'Save'}</button>{/if}
        <button type="button" class="request-action ghost" disabled={draft.busy} onclick={() => void action('reject')}>Reject</button>
        <span class="footer-space"></span>
        {#if current.help || current.help_url}<button type="button" class="quiet-affordance" aria-label="API key help" aria-expanded={draft.helpOpen} aria-controls={`${uid}-help`} onclick={() => draft.helpOpen = !draft.helpOpen}>?</button>{/if}
      </div>
      {#if replacement}<p class="replacement-note">Replaces this key everywhere it is used.</p>{/if}
      {#if draft.helpOpen}<div class="key-help" id={`${uid}-help`}>{current.help ?? ''} {#if current.help_url}<a href={current.help_url} target="_blank" rel="noreferrer">{current.provider} guide ↗</a>{/if}</div>{/if}
      {#if draft.error}<p id={`${uid}-error`} class="error-note" role="alert">{draft.error}</p>{/if}
      {#if draft.stale}<button bind:this={reviewButton} type="button" class="request-action ghost review" disabled={draft.busy} onclick={() => void action('review')}>Review current key</button>{/if}
      {#if draft.reviewed}<p class="state-note">Current key · updated just now</p>{/if}
    </form>
  {/if}
</section>

<style>
  .key-request{align-self:flex-start;width:100%;max-width:calc(var(--feedback-capture-width) + var(--space-7)*2);min-width:0;box-sizing:border-box;background:var(--surface-2);border:0;border-radius:var(--radius-lg);padding:var(--space-5);font-size:var(--type-sm);overflow-wrap:anywhere}
  form{display:grid;gap:var(--space-4);margin:0;min-width:0}
  .stacked-fields{display:grid;gap:var(--space-3)}
  .key-name,.fixed-name{box-sizing:border-box;min-width:0;width:100%;padding:var(--space-3);min-height:var(--space-7);background:var(--surface-input);border:0;border-radius:var(--radius-md);color:var(--text-default);font-family:var(--font-ui);font-size:var(--type-input);line-height:1.5}
  .fixed-name{display:flex;align-items:center;overflow-wrap:anywhere}
  .key-name:hover{color:var(--text-strong)}
  .private-input{min-width:0;display:flex;align-items:center;background:var(--surface-input);border:0;border-radius:var(--radius-md)}
  .private-input input{width:100%;min-width:0;box-sizing:border-box;min-height:var(--space-7);padding:var(--space-3);border:0;border-radius:var(--radius-md);background:transparent;color:var(--text-strong);font-family:var(--font-ui);font-size:var(--type-input)}
  .private-input input::placeholder{color:var(--text-faintest)}
  .request-action{font:inherit;font-size:var(--type-sm);font-weight:500;line-height:1.6;padding:var(--space-2) calc(var(--space-4) + var(--border-strong));border:0;border-radius:var(--radius-pill);cursor:pointer}
  button:disabled{opacity:.5;cursor:default}
  .primary{background:var(--accent-bright);color:var(--surface-ink)}
  .ghost{background:transparent;color:var(--text-faint);padding:var(--space-2) var(--space-3)}
  .ghost:hover{background-image:var(--interaction-hover);color:var(--text-strong)}
  .quiet-affordance{display:inline-flex;align-items:center;justify-content:center;font:inherit;line-height:1.6;padding:var(--space-2);border:0;border-radius:var(--radius-pill);background:transparent;color:var(--text-faint);cursor:pointer}
  .quiet-affordance:hover{color:var(--text-strong)}
  .reveal-button{flex:none;margin-right:var(--space-2)}
  svg{width:var(--space-4);height:var(--space-4);fill:none;stroke:currentColor;stroke-width:var(--border-hairline);stroke-linecap:round;stroke-linejoin:round}
  .form-footer{display:flex;gap:var(--space-4);align-items:center;margin-top:var(--space-1)}
  .footer-space{flex:1}
  .key-help{color:var(--text-faint);font-size:var(--type-sm);line-height:1.6}
  .key-help a{color:var(--accent-bright);white-space:nowrap}
  .replacement-note,.state-note,.error-note{margin:0;font-size:var(--type-sm);line-height:1.5;color:var(--text-faint)}
  .error-note{color:var(--accent-error)}
  .saved-row{display:flex;align-items:baseline;gap:var(--space-3);min-width:0}
  .saved-name{color:var(--text-muted);min-width:0;overflow-wrap:anywhere}
  .saved-status{color:var(--text-faint);flex:none;font-size:var(--type-xs)}
  .result-status>.state-note,.delivery-state{margin-top:var(--space-2)}
  .delivery-state .request-action{margin-top:var(--space-2)}
  .result-status:focus{outline:none}
  @media(max-width:560px){.key-request{padding:var(--space-4)}.key-name,.fixed-name,.private-input input{font-size:var(--space-4)}}
</style>
