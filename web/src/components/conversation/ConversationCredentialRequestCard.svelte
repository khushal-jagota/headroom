<script lang="ts">
  import { getContext, onMount, tick, untrack } from 'svelte';
  import type { CredentialRequestRowBody } from '../../lib/conversation/view/contracts';
  import { CREDENTIAL_REQUEST_READ_ONLY, CREDENTIAL_REQUEST_DRAFTS, CredentialRequestDraft, type CredentialRequestDrafts } from '../../lib/conversation/credentialRequest.svelte';
  import { CredentialRequestWireError, mutateCredentialRequest } from '../../lib/conversation/wire';
  import AskShell from './AskShell.svelte';
  import SystemLine from './SystemLine.svelte';

  let { request, framed = true, onStop }: {
    request: CredentialRequestRowBody;
    /** Whether the ask draws its own raised plane (the composer gives up its own for it). */
    framed?: boolean;
    /** Ends the turn that waits on this key, as the ask's last row. */
    onStop?: () => void;
  } = $props();
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
      draft.clearSecret(); draft.helpOpen = false;
      if (draft.local?.state === 'pending') draft.local = null;
    }
    if (request === previousRequest) return;
    if (request.request_id !== previousRequest.request_id) {
      draft.name = request.name; draft.clearSecret(); draft.helpOpen = false;
      draft.error = ''; draft.invalid = null; draft.stale = false; draft.saveFailed = false; draft.reviewed = false;
    }
    previousRequest = request;
    draft.local = null;
    if (request.state !== 'pending') {
      draft.clearSecret(); draft.helpOpen = false;
    }
  });
  const replacement = $derived(current.replacement !== null);
  /** A fields request asks for several named values in place of one key. */
  const requestedFields = $derived(current.auth_kind === 'fields' ? current.fields ?? [] : null);
  const hasSecret = $derived(requestedFields
    ? requestedFields.every((field) => !!draft.fields[field.name]?.trim())
    : !!draft.secret.trim());
  /** Save goes once there is a key to save, as a question's Next goes once there is an answer. */
  const canSave = $derived(hasSecret && (replacement || !!draft.name.trim()));
  function enterField(name: string, value: string): void {
    draft.fields = { ...draft.fields, [name]: value };
    corrected('secret', value);
  }
  /** A file field reads the file here and keeps only its text in memory, never in storage. */
  async function chooseFile(name: string, input: HTMLInputElement): Promise<void> {
    const target = draft;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    // The server keeps at most 16384 characters per field; a larger file is not a key.
    const text = file.size > 65536 ? null : await file.text();
    // A save, decline or another request can settle while the file reads.
    if (target !== draft || target.busy || current.state !== 'pending') return;
    if (text === null || Array.from(text).length > 16384) {
      target.error = 'That file is too large for a key.'; target.invalid = 'secret'; return;
    }
    target.files = { ...target.files, [name]: file.name };
    enterField(name, text);
  }
  /** The one key input, or the first field that still needs a value. */
  function focusSecret(): void {
    if (!requestedFields) { secretInput?.focus(); return; }
    const empty = requestedFields.find((field) => !draft.fields[field.name]?.trim()) ?? requestedFields[0];
    if (empty) root?.querySelector<HTMLElement>(`[data-credential-field="${empty.name}"]`)?.focus();
  }
  /** The record says what the person did, in the button's word: Decline is "Declined". */
  const outcome = $derived(current.state === 'rejected' ? 'Declined' : current.state === 'replaced' ? 'Replaced' : 'Saved');
  function ownsFocus(): boolean { return !!root?.contains(document.activeElement); }
  async function focusResult(): Promise<void> { await tick(); if (ownsFocus() || document.activeElement === document.body) result?.focus(); }
  function corrected(field: 'name' | 'secret', value: string): void {
    if (draft.invalid === field && value.trim()) { draft.invalid = null; draft.error = ''; }
    if (draft.saveFailed) { draft.saveFailed = false; draft.error = ''; }
  }
  async function invalidField(field: 'name' | 'secret', message: string): Promise<void> {
    draft.invalid = field; draft.error = message; draft.saveFailed = false;
    await tick();
    if (field === 'name') nameInput?.focus();
    else focusSecret();
  }
  function inputKey(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); void submit(); }
  }
  async function submit(): Promise<void> {
    const submittingDraft = draft;
    if (readOnly || submittingDraft.busy || submittingDraft.stale || current.state !== 'pending') return;
    if (!submittingDraft.name.trim()) return invalidField('name', 'Enter a key name.');
    if (!hasSecret) return invalidField('secret', requestedFields ? 'Enter every field.' : 'Paste the API key.');
    const fields = requestedFields;
    const value = fields
      ? Object.fromEntries(fields.map((field) => [field.name, submittingDraft.fields[field.name] ?? '']))
      : submittingDraft.secret;
    const ownedBefore = ownsFocus();
    submittingDraft.busy = true; submittingDraft.error = ''; submittingDraft.invalid = null; submittingDraft.saveFailed = false;
    try {
      const resolved = await mutateCredentialRequest(current.request_id, 'save', {
        name: replacement ? current.name : submittingDraft.name.trim(), value,
        expected_revision: current.replacement?.revision ?? null
      });
      const focus = ownsFocus() || ownedBefore && document.activeElement === document.body;
      submittingDraft.local = resolved; submittingDraft.clearSecret(); submittingDraft.helpOpen = false;
      if (focus) await focusResult();
    } catch (reason) {
      if (reason instanceof CredentialRequestWireError && reason.stale) {
        const focus = ownsFocus() || ownedBefore && document.activeElement === document.body;
        submittingDraft.clearSecret(); submittingDraft.stale = true; submittingDraft.busy = false;
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
        submittingDraft.clearSecret(); submittingDraft.stale = false; submittingDraft.saveFailed = false; submittingDraft.reviewed = true;
        await tick(); if (focus) focusSecret();
      } else {
        submittingDraft.clearSecret(); submittingDraft.helpOpen = false;
        if (focus) await focusResult();
      }
    } catch (reason) { submittingDraft.error = reason instanceof Error ? reason.message : 'The request could not be completed.'; }
    finally { submittingDraft.busy = false; }
  }
</script>

{#snippet answers()}
  <button type="button" class="cv-button cv-button--outline reject" data-credential-decline disabled={draft.busy} onclick={() => void action('reject')}>Decline</button>
  {#if !draft.stale}<button type="button" class="cv-button cv-button--primary" data-credential-save disabled={draft.busy || !canSave} onclick={() => void submit()}>{draft.busy ? 'Saving…' : draft.saveFailed ? 'Retry' : replacement ? 'Replace key' : 'Save key'}</button>{/if}
{/snippet}

<section bind:this={root} class="key-request" class:is-ask={current.state === 'pending' && !readOnly} aria-label={`${current.provider} API key request`} data-conversation-row="credential_request" data-credential-request={current.request_id} data-credential-state={current.state}>
  {#if current.state !== 'pending'}
    <div bind:this={result} class="result-status" role="status" tabindex="-1">
      <!-- The shared system line: its dot leads the words inside the column. -->
      <SystemLine mark="dot" data-credential-record="">{current.provider} API key · <span class="saved-name">{current.credential?.reference.name ?? current.name}</span> · {outcome}</SystemLine>
      {#if current.delivery?.status === 'queued'}<p class="state-note">Result queued for the agent.</p>
      {:else if current.delivery?.status === 'refused'}
        <div class="delivery-state"><p class="state-note">Result did not reach the agent.</p>
          {#if !readOnly}<button class="cv-button cv-button--secondary cv-button--small" type="button" disabled={draft.busy} onclick={() => void action('retry-delivery')}>{draft.busy ? 'Sending…' : 'Retry delivery'}</button>{/if}
        </div>
      {:else if current.delivery?.status === 'uncertain'}<p class="state-note is-caution">Delivery is uncertain. Check the conversation before another attempt.</p>
      {:else if current.delivery?.status === 'canceled'}<p class="state-note">Result delivery canceled.</p>{/if}
      {#if draft.error}<p class="error-note" role="alert">{draft.error}</p>{/if}
    </div>
  {:else if readOnly}
    <SystemLine mark="dot" data-credential-record="">{current.provider} API key · <span class="saved-name">{current.name}</span> · Requested</SystemLine>
  {:else}
    <!-- The ask shell every blocking ask shares (AskShell): the accent eyebrow saying what it
         needs, the title, the fields, then the answers ending in the one primary. -->
    <AskShell
      kind="credential"
      eyebrow="Needs a key"
      title={replacement ? `Replace the ${current.provider} API key` : `${current.provider} API key`}
      titleId={`${uid}-title`}
      description={current.use}
      {framed}
      {onStop}
      footerEnd={answers}
    >
    <form autocomplete="off" aria-labelledby={`${uid}-title`} aria-describedby={draft.error && !draft.invalid ? `${uid}-error` : undefined}
      onsubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div class="stacked-fields">
        <!-- Each field says what it is: the name the key is saved under (in the mono of a
             variable, editable unless it replaces one) and the secret itself. -->
        <div class="field">
          <span class="field-label" aria-hidden={replacement ? undefined : 'true'}>Key name</span>
          {#if replacement}<span class="fixed-name">{current.name}</span>
          {:else}<input bind:this={nameInput} bind:value={draft.name} class="key-name" aria-label="Key name" placeholder="KEY_NAME" autocomplete="off" spellcheck="false" disabled={draft.busy} aria-invalid={draft.invalid === 'name' ? 'true' : undefined} aria-describedby={draft.invalid === 'name' ? `${uid}-error` : undefined} onkeydown={inputKey} oninput={(event) => corrected('name', event.currentTarget.value)} />{/if}
        </div>
        {#if !draft.stale && requestedFields}
          <!-- A fields request: one private input per named value; a file field takes a file. -->
          {#each requestedFields as field (field.name)}
            <div class="field">
              <span class="field-label" aria-hidden="true">{field.name}</span>
              {#if field.file}
                <div class="file-input">
                  <input type="file" hidden data-credential-field-file={field.name} disabled={draft.busy} onchange={(event) => void chooseFile(field.name, event.currentTarget)} />
                  <button type="button" class="cv-button cv-button--secondary cv-button--small" data-credential-field={field.name} aria-label={`Choose a file for ${field.name}`} aria-describedby={draft.invalid === 'secret' ? `${uid}-error` : undefined} disabled={draft.busy} onclick={(event) => (event.currentTarget.previousElementSibling as HTMLInputElement | null)?.click()}>{draft.files[field.name] ? 'Choose another file' : 'Choose file'}</button>
                  <span class="file-name">{draft.files[field.name] ?? 'No file chosen'}</span>
                </div>
              {:else}
                <div class="private-input"><input type="password" value={draft.fields[field.name] ?? ''} data-credential-field={field.name} aria-label={field.name} autocomplete="off" spellcheck="false" disabled={draft.busy} aria-invalid={draft.invalid === 'secret' && !draft.fields[field.name]?.trim() ? 'true' : undefined} aria-describedby={draft.invalid === 'secret' ? `${uid}-error` : undefined} onkeydown={inputKey} oninput={(event) => enterField(field.name, event.currentTarget.value)} /></div>
              {/if}
            </div>
          {/each}
        {:else if !draft.stale}
          <div class="field">
            <span class="field-label" aria-hidden="true">{replacement ? 'New API key' : 'API key'}</span>
            <div class="private-input"><input bind:this={secretInput} bind:value={draft.secret} type={draft.revealed ? 'text' : 'password'} aria-label={replacement ? 'New API key' : 'API key'} placeholder={replacement ? 'Paste the new key' : 'Paste the key'} autocomplete="off" spellcheck="false" disabled={draft.busy} aria-invalid={draft.invalid === 'secret' ? 'true' : undefined} aria-describedby={draft.invalid === 'secret' ? `${uid}-error` : undefined} onkeydown={inputKey} oninput={(event) => corrected('secret', event.currentTarget.value)} />
              {#if draft.secret}<button type="button" class="cv-icon-button cv-icon-button--small reveal-button" aria-label={draft.revealed ? 'Hide API key' : 'Show API key'} aria-pressed={draft.revealed} disabled={draft.busy} onclick={() => draft.revealed = !draft.revealed}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>{#if draft.revealed}<path d="m3 3 18 18"/>{/if}</svg></button>{/if}
            </div>
          </div>
        {/if}
      </div>
      {#if replacement}<p class="replacement-note">Replaces this key everywhere it is used.</p>{/if}
      {#if current.help || current.help_url}
        <div class="help">
          <button type="button" class="help-toggle" aria-expanded={draft.helpOpen} aria-controls={`${uid}-help`} onclick={() => draft.helpOpen = !draft.helpOpen}>How to get a key<svg class="help-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg></button>
          {#if draft.helpOpen}<div class="key-help" id={`${uid}-help`}>{current.help ?? ''} {#if current.help_url}<a href={current.help_url} target="_blank" rel="noreferrer">{current.provider} guide ↗</a>{/if}</div>{/if}
        </div>
      {/if}
      {#if draft.error}<p id={`${uid}-error`} class="error-note" role="alert">{draft.error}</p>{/if}
      {#if draft.stale}<button bind:this={reviewButton} type="button" class="cv-button cv-button--secondary review" disabled={draft.busy} onclick={() => void action('review')}>Review current key</button>{/if}
      {#if draft.reviewed}<p class="state-note">Current key · updated just now</p>{/if}
    </form>
    </AskShell>
  {/if}
</section>

<style>
  /* A settled request is a quiet line in the transcript; a pending one is the ask shell. */
  .key-request{width:100%;min-width:0;box-sizing:border-box;overflow-wrap:anywhere;font-family:var(--cv-font)}
  form{display:grid;gap:var(--cv-s3);margin:0;min-width:0}
  .stacked-fields{display:grid;gap:var(--cv-s3)}
  .field{display:grid;gap:6px;min-width:0}
  .field-label{color:var(--cv-t2);font:var(--cv-weight-label) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font)}
  /* Fields are canvas wells inside the raised ask: the field spec (h40, r8, a hairline). */
  .key-name,.private-input{box-sizing:border-box;min-width:0;width:100%;min-height:40px;background:var(--cv-canvas);border:1px solid var(--cv-line-strong);border-radius:var(--cv-r-md)}
  .key-name{padding:0 var(--cv-s3);color:var(--cv-t1);font:var(--cv-weight-body) var(--cv-type-code)/var(--cv-leading-code) var(--cv-mono)}
  .key-name::placeholder,.private-input input::placeholder{color:var(--cv-placeholder)}
  .key-name:focus,.private-input:focus-within{outline:none;border-color:var(--cv-accent-line)}
  /* A fixed name is a value, not a field: mono t1 on a control chip. */
  .fixed-name{justify-self:start;padding:2px var(--cv-s2);border-radius:var(--cv-r-sm);background:var(--cv-control);color:var(--cv-t1);font:var(--cv-weight-body) var(--cv-type-code)/var(--cv-leading-code) var(--cv-mono);overflow-wrap:anywhere}
  .private-input{display:flex;align-items:center}
  .private-input input{width:100%;min-width:0;box-sizing:border-box;min-height:38px;padding:0 var(--cv-s3);border:0;background:transparent;color:var(--cv-t1);font:var(--cv-weight-body) var(--cv-type-ui)/var(--cv-leading-ui) var(--cv-font)}
  .private-input input:focus{outline:none}
  .reveal-button{margin-right:6px}
  .file-input{display:flex;flex-wrap:wrap;align-items:center;gap:var(--cv-s2);min-width:0}
  .file-name{min-width:0;overflow-wrap:anywhere;color:var(--cv-t2);font:var(--cv-weight-body) var(--cv-type-code)/var(--cv-leading-code) var(--cv-mono)}
  .help{display:grid;gap:var(--cv-s2);justify-items:start}
  /* A link, and reads as one: t1 with the soft underline every conversation link wears. */
  .help-toggle{display:inline-flex;align-items:center;gap:2px;min-height:24px;padding:0;border:0;border-radius:var(--cv-r-sm);background:transparent;color:var(--cv-t1);font:var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);text-decoration:underline;text-decoration-color:var(--cv-link-underline);text-underline-offset:3px;cursor:pointer}
  .help-toggle:hover{text-decoration-color:currentColor}
  .help-toggle:focus-visible{outline:var(--cv-focus-width) solid var(--cv-accent);outline-offset:var(--cv-focus-offset)}
  .help-toggle .help-chevron{width:12px;height:12px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;transition:rotate var(--cv-dur-fast) var(--cv-ease-out)}
  .help-toggle[aria-expanded="true"] .help-chevron{rotate:90deg}
  @media (prefers-reduced-motion:reduce){.help-toggle .help-chevron{transition:none}}
  .review{justify-self:start}
  .key-help{color:var(--cv-t3);font:var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font)}
  .key-help a{color:var(--cv-t1);white-space:nowrap;text-decoration-color:var(--cv-link-underline);text-underline-offset:3px}
  .replacement-note,.state-note,.error-note{margin:0;font:var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);color:var(--cv-t3)}
  .state-note.is-caution{color:var(--cv-caution)}
  .error-note{color:var(--cv-danger)}
  /* Settled: the one system line. The key's name keeps the mono of a variable, on the line's
     20 px leading and in its ink. */
  .saved-name{overflow-wrap:anywhere;font-family:var(--cv-mono);font-size:var(--cv-type-code);line-height:1}
  .result-status>.state-note,.delivery-state{margin-top:var(--cv-s2)}
  .delivery-state{display:flex;flex-wrap:wrap;align-items:center;gap:var(--cv-s2)}
  .result-status:focus{outline:none}
  @media(max-width:560px){.private-input input,.key-name{font-size:16px}}
</style>
