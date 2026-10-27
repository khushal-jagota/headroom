<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import { fetchJson } from '../api';
  import { queryClient } from '../queryClient';
  import { clearSecretFields, emptySecretFields, secretBody } from './credentialForm';
  import { fixedWriteError } from './presentation';
  import type { CredentialAuthKind, CredentialMetadata, CredentialScope, CreateCredentialRequest, ReplaceCredentialRequest } from './wire';

  let { scope, existing = null, suggestedName = '', suggestedOrigins = [], onclose }: {
    scope: CredentialScope; existing?: CredentialMetadata | null; suggestedName?: string;
    suggestedOrigins?: string[]; onclose: () => void;
  } = $props();
  let name = $state(untrack(() => existing?.reference.name ?? suggestedName));
  let origins = $state(untrack(() => (existing?.allowed_origins ?? suggestedOrigins).join('\n')));
  let kind = $state<CredentialAuthKind>(untrack(() => existing?.auth_kind ?? 'bearer'));
  let fields = $state(emptySecretFields());
  let saving = $state(false);
  let error = $state('');
  let alive = true;
  const abort = new AbortController();
  function close() { clearSecretFields(fields); abort.abort(); onclose(); }
  onDestroy(() => { alive = false; clearSecretFields(fields); abort.abort(); });

  async function save(event: SubmitEvent) {
    event.preventDefault();
    saving = true; error = '';
    try {
      const body: CreateCredentialRequest | ReplaceCredentialRequest = existing ? {
        expected_revision: existing.revision, secret: secretBody(kind, fields)
      } : {
        project_id: scope.project_id, account: scope.account, scheme: 'local', name,
        auth_kind: kind, allowed_origins: origins.split(/\s+/).filter(Boolean), secret: secretBody(kind, fields)
      };
      await fetchJson<CredentialMetadata>(existing ? `/api/credentials/${encodeURIComponent(existing.id)}/secret` : '/api/credentials', {
        method: existing ? 'PUT' : 'POST', body, signal: abort.signal
      });
      clearSecretFields(fields);
      if (alive) onclose();
      await queryClient.invalidateQueries();
    } catch (failure) {
      if (alive) error = fixedWriteError(failure);
    } finally { if (alive) saving = false; }
  }
</script>

<form class="credential-form" onsubmit={save} autocomplete="off" aria-label={existing ? 'Replace credential' : 'Add credential'}>
  <h3>{existing ? 'Replace credential' : 'Add credential'}</h3>
  <p>Account: {scope.account}. The secret is used by the host and is never shown again.</p>
  <label>Credential name <input required maxlength="120" bind:value={name} disabled={!!existing || saving} /></label>
  <label>Allowed HTTPS origins <textarea required rows="2" bind:value={origins} disabled={!!existing || saving} placeholder="https://api.example.com"></textarea></label>
  <label>Authentication <select bind:value={kind} disabled={!!existing || saving} onchange={() => clearSecretFields(fields)}>
    <option value="bearer">Bearer token</option><option value="header">API key header</option><option value="basic">Basic username and password</option>
  </select></label>
  {#if kind === 'bearer'}
    <label>Token <input type="password" required autocomplete="new-password" bind:value={fields.token} disabled={saving} /></label>
  {:else if kind === 'header'}
    <label>Header name <input required bind:value={fields.headerName} disabled={saving} placeholder="X-Api-Key" /></label>
    <label>API key <input type="password" required autocomplete="new-password" bind:value={fields.value} disabled={saving} /></label>
  {:else}
    <label>Username <input type="password" required autocomplete="new-password" bind:value={fields.username} disabled={saving} /></label>
    <label>Password <input type="password" autocomplete="new-password" bind:value={fields.password} disabled={saving} /></label>
  {/if}
  {#if error}<p role="alert">{error}</p>{/if}
  <div class="actions"><button disabled={saving} type="submit">{saving ? 'Saving…' : 'Save credential'}</button><button type="button" onclick={close}>Cancel</button></div>
</form>

<style>
  .credential-form { display:grid; gap:var(--space-3); padding:var(--space-4); border:var(--border-hairline) solid var(--border-color); }
  label { display:grid; gap:var(--space-1); } input, select, textarea { width:100%; max-width:32rem; box-sizing:border-box; }
  h3,p { margin:0; } p { color:var(--text-muted); } .actions { display:flex; gap:var(--space-2); }
  [role=alert] { color:var(--text-strong); }
</style>
