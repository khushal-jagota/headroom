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

<section bind:this={root} class="key-request" class:is-ask={current.state === 'pending' && !readOnly} aria-label={`${current.provider} API key request`} data-conversation-row="credential_request" data-credential-request={current.request_id} data-credential-state={current.state}>
  {#if current.state !== 'pending'}
    <div bind:this={result} class="result-status" role="status" tabindex="-1">
      <div class="saved-row"><span class="saved-provider">{current.provider} API key</span><span class="saved-name">{current.credential?.reference.name ?? current.name}</span><span class="saved-status">{outcome}</span></div>
      {#if current.delivery?.status === 'queued'}<p class="state-note">Result queued for the agent.</p>
      {:else if current.delivery?.status === 'refused'}
        <div class="delivery-state"><p class="state-note">Result did not reach the agent.</p>
          {#if !readOnly}<button class="button button--secondary" type="button" disabled={draft.busy} onclick={() => void action('retry-delivery')}>{draft.busy ? 'Sending…' : 'Retry delivery'}</button>{/if}
        </div>
      {:else if current.delivery?.status === 'uncertain'}<p class="state-note">Delivery is uncertain. Check the conversation before another attempt.</p>
      {:else if current.delivery?.status === 'canceled'}<p class="state-note">Result delivery canceled.</p>{/if}
      {#if draft.error}<p class="error-note" role="alert">{draft.error}</p>{/if}
    </div>
  {:else if readOnly}
    <div class="saved-row"><span class="saved-provider">{current.provider} API key</span><span class="saved-name">{current.name}</span><span class="saved-status">Requested</span></div>
  {:else}
    <!-- The ask shell every blocking ask shares: an accent label saying what it needs, the
         title under it, the body, then the action row with the one primary at its right end. -->
    <header class="request-head">
      <span class="request-eyebrow">Needs your key</span>
      <h3 class="request-title" id={`${uid}-title`}>{replacement ? `Replace the ${current.provider} API key` : `${current.provider} API key`}</h3>
      {#if current.use}<p class="request-use">{current.use}</p>{/if}
    </header>
    <form autocomplete="off" aria-labelledby={`${uid}-title`} aria-describedby={draft.error && !draft.invalid ? `${uid}-error` : undefined}
      onsubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div class="stacked-fields">
        <!-- Each field says what it is: the name the key is saved under (editable, in the
             mono of a variable) and the secret itself. -->
        <div class="field">
          <span class="field-label" aria-hidden={replacement ? undefined : 'true'}>Key name</span>
          {#if replacement}<span class="fixed-name">{current.name}</span>
          {:else}<input bind:this={nameInput} bind:value={draft.name} class="key-name" aria-label="Key name" placeholder="KEY_NAME" autocomplete="off" spellcheck="false" disabled={draft.busy} aria-invalid={draft.invalid === 'name' ? 'true' : undefined} aria-describedby={draft.invalid === 'name' ? `${uid}-error` : undefined} onkeydown={inputKey} oninput={(event) => corrected('name', event.currentTarget.value)} />{/if}
        </div>
        {#if !draft.stale}
          <div class="field">
            <span class="field-label" aria-hidden="true">{replacement ? 'New API key' : 'API key'}</span>
            <div class="private-input"><input bind:this={secretInput} bind:value={draft.secret} type={draft.revealed ? 'text' : 'password'} aria-label={replacement ? 'New API key' : 'API key'} placeholder={replacement ? 'Paste the new key' : 'Paste the key'} autocomplete="off" spellcheck="false" disabled={draft.busy} aria-invalid={draft.invalid === 'secret' ? 'true' : undefined} aria-describedby={draft.invalid === 'secret' ? `${uid}-error` : undefined} onkeydown={inputKey} oninput={(event) => corrected('secret', event.currentTarget.value)} />
              {#if draft.secret}<button type="button" class="quiet-affordance reveal-button" aria-label={draft.revealed ? 'Hide API key' : 'Show API key'} aria-pressed={draft.revealed} disabled={draft.busy} onclick={() => draft.revealed = !draft.revealed}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>{#if draft.revealed}<path d="m3 3 18 18"/>{/if}</svg></button>{/if}
            </div>
          </div>
        {/if}
      </div>
      {#if replacement}<p class="replacement-note">Replaces this key everywhere it is used.</p>{/if}
      {#if current.help || current.help_url}
        <div class="help">
          <button type="button" class="help-toggle" aria-expanded={draft.helpOpen} aria-controls={`${uid}-help`} onclick={() => draft.helpOpen = !draft.helpOpen}>How to get a key</button>
          {#if draft.helpOpen}<div class="key-help" id={`${uid}-help`}>{current.help ?? ''} {#if current.help_url}<a href={current.help_url} target="_blank" rel="noreferrer">{current.provider} guide ↗</a>{/if}</div>{/if}
        </div>
      {/if}
      {#if draft.error}<p id={`${uid}-error`} class="error-note" role="alert">{draft.error}</p>{/if}
      {#if draft.stale}<button bind:this={reviewButton} type="button" class="button button--secondary review" disabled={draft.busy} onclick={() => void action('review')}>Review current key</button>{/if}
      {#if draft.reviewed}<p class="state-note">Current key · updated just now</p>{/if}
      <!-- THE button (app.css, "Buttons"), in the ask shell's order: the ghost that declines,
           then the one primary at the right end, as a permission ask ends in Allow once. -->
      <div class="form-footer">
        <button type="button" class="button button--ghost reject" disabled={draft.busy} onclick={() => void action('reject')}>Reject</button>
        {#if !draft.stale}<button type="button" class="button button--primary" disabled={draft.busy} onclick={() => void submit()}>{draft.busy ? 'Saving…' : draft.saveFailed ? 'Retry' : replacement ? 'Replace' : 'Save'}</button>{/if}
      </div>
    </form>
  {/if}
</section>

<style>
  /* The column's full width, as every ask is. A pending request stands on the composer's own
     plane (its fill, edge and radius), the shell the question and the permission ask are
     drawn on; a settled one is a quiet line on the transcript's card fill. */
  .key-request{width:100%;min-width:0;box-sizing:border-box;background:var(--surface-2);border:0;border-radius:var(--composer-radius);padding:var(--space-3) var(--space-4);font-size:var(--type-sm);overflow-wrap:anywhere}
  .key-request.is-ask{background:var(--composer-surface);border:var(--border-hairline) solid var(--composer-edge);padding:var(--space-4) var(--space-3) var(--space-3) calc(var(--composer-inset) - var(--border-hairline))}
  .request-head{display:grid;gap:var(--space-1);margin-bottom:var(--space-3)}
  .request-eyebrow{color:var(--accent-bright);font-family:var(--font-ui);font-size:var(--type-xs);font-weight:var(--weight-title);letter-spacing:var(--tracking-label);text-transform:uppercase}
  .request-title{margin:0;font:inherit;font-size:var(--type-md);font-weight:var(--weight-title);line-height:1.4;color:var(--text-strong)}
  .request-use{margin:0;color:var(--text-muted);line-height:1.5}
  form{display:grid;gap:var(--space-3);margin:0;min-width:0}
  .stacked-fields{display:grid;gap:var(--space-3)}
  .field{display:grid;gap:var(--space-1);min-width:0}
  .field-label{color:var(--text-muted);font-size:var(--type-xs)}
  .key-name,.fixed-name{box-sizing:border-box;min-width:0;width:100%;padding:var(--space-2) var(--space-3);min-height:40px;background:transparent;border:var(--border-hairline) solid var(--border-raised);border-radius:var(--radius-md);color:var(--text-strong);font-family:var(--font-mono);font-size:var(--type-code);letter-spacing:var(--tracking-mono);line-height:1.5}
  .key-name::placeholder{color:var(--text-muted)}
  /* A fixed name is a value, not a field: no fill, so it cannot be mistaken for one. */
  .fixed-name{display:flex;align-items:center;overflow-wrap:anywhere;background:transparent;border:0;padding-inline:0;min-height:0}
  .key-name:hover{background:var(--state-step)}
  /* The fields are the question's own answer field: a hairline on the composer's fill, since
     the input fill is that fill and drew nothing there. */
  .private-input{min-width:0;display:flex;align-items:center;background:transparent;border:var(--border-hairline) solid var(--border-raised);border-radius:var(--radius-md)}
  .private-input input{width:100%;min-width:0;box-sizing:border-box;min-height:40px;padding:var(--space-2) var(--space-3);border:0;border-radius:var(--radius-md);background:transparent;color:var(--text-strong);font-family:var(--font-ui);font-size:var(--type-input);line-height:1.5}
  .private-input input::placeholder{color:var(--text-muted)}
  .private-input:hover{background:var(--state-step)}
  .quiet-affordance{display:inline-flex;align-items:center;justify-content:center;min-width:24px;min-height:24px;font:inherit;line-height:1.6;padding:var(--space-1);border:0;border-radius:var(--radius-sm);background:transparent;color:var(--text-muted);cursor:pointer}
  .quiet-affordance:hover{color:var(--text-strong)}
  .reveal-button{flex:none;margin-right:var(--space-2)}
  svg{width:var(--space-4);height:var(--space-4);fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}
  .help{display:grid;gap:var(--space-2);justify-items:start}
  .help-toggle{min-height:24px;padding:0;border:0;border-radius:var(--radius-sm);background:transparent;color:var(--text-muted);font:inherit;font-size:var(--type-xs);text-decoration:underline;text-decoration-color:var(--border-raised);text-underline-offset:3px;cursor:pointer}
  .help-toggle:hover{color:var(--text-strong)}
  .form-footer{display:flex;flex-wrap:wrap;gap:var(--space-2);align-items:center;justify-content:flex-end}
  .form-footer .button--ghost:not(:disabled){color:var(--text-default)}
  .form-footer .button--ghost:hover:not(:disabled){color:var(--text-strong)}
  .form-footer .reject:hover:not(:disabled){color:var(--accent-error)}
  .review{justify-self:start;background:var(--state-step)}
  .review:hover:not(:disabled){background:var(--state-step);background-image:var(--interaction-hover)}
  .key-help{color:var(--text-muted);font-size:var(--type-sm);line-height:1.6}
  .key-help a{color:var(--accent-bright);white-space:nowrap}
  .replacement-note,.state-note,.error-note{margin:0;font-size:var(--type-sm);line-height:1.5;color:var(--text-muted)}
  .error-note{color:var(--accent-error)}
  .saved-row{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:var(--space-3);row-gap:var(--space-1);min-width:0}
  .saved-provider{color:var(--text-default);flex:none}
  .saved-name{color:var(--text-muted);min-width:0;overflow-wrap:anywhere;font-family:var(--font-mono);font-size:var(--type-code)}
  .saved-status{color:var(--text-muted);flex:none;font-size:var(--type-xs);margin-left:auto}
  .result-status>.state-note,.delivery-state{margin-top:var(--space-2)}
  .delivery-state .button{margin-top:var(--space-2)}
  .result-status:focus{outline:none}
  @media(max-width:560px){.key-request.is-ask{padding:var(--space-4) var(--space-3) var(--space-3)}.private-input input{font-size:var(--space-4)}.key-name{font-size:var(--space-4)}}
</style>
