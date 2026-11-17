<script lang="ts">
  /* Signing a backend in from Settings. It shows only for a backend Panels can sign in,
   * and only while its account is signed out or a sign-in is in progress, so an account
   * that already works looks exactly as it did. */
  import {
    BACKENDS_PAGE_SURROUNDINGS,
    backendCanBeSignedIn,
    signInCaveat,
    signInIsPending,
    signInOutcome
  } from "../../lib/conversation/backendSignIn";
  import type { SignInPageState } from "../../lib/conversation/backendSignInSession";
  import type { BackendSnapshot } from "../../lib/conversation/wire";

  let { label, snapshot, state: signInState, onStart, onCancel, onSubmit }: {
    label: string;
    snapshot: BackendSnapshot;
    state: SignInPageState;
    onStart: () => void;
    onCancel: () => void;
    onSubmit: (returnUrl: string) => void;
  } = $props();

  let returnUrl = $state("");
  /** The pasted address belongs to one waiting session. A sign-in that moves on, or a new
   *  one, starts with an empty box rather than an old OAuth return address. */
  const waitingSession = $derived(signInState.signIn?.state === "awaiting_return_url"
    ? signInState.signIn.session_id : null);
  let returnUrlSession: string | null = null;
  $effect(() => {
    if (waitingSession === returnUrlSession) return;
    returnUrlSession = waitingSession;
    returnUrl = "";
  });
  const shows = $derived(backendCanBeSignedIn(snapshot)
    && (snapshot.identity?.status === "unauthenticated" || signInIsPending(signInState.signIn)));
</script>

{#if shows}
  {@const signIn = signInState.signIn}
  {@const outcome = signInOutcome(signIn, label)}
  <div class="sign-in" data-backend-sign-in={snapshot.backend_key}>
    {#if signIn?.state === "pending"}
      <p class="sign-in-step">
        1. Open
        <a href={signIn.verification_url ?? "#"} target="_blank" rel="noreferrer noopener"
          data-backend-sign-in-url>{signIn.verification_url}</a>
        and sign in.
      </p>
      <p class="sign-in-step">
        2. Enter this one-time code:
        <b data-backend-sign-in-code>{signIn.user_code}</b>
      </p>
      <p class="sign-in-caveat">
        Panels stops showing this code after its own time limit. It cannot promise
        the code stays valid until then.
      </p>
      <button
        class="sign-in-action"
        disabled={signInState.busy}
        onclick={onCancel}
        data-backend-sign-in-cancel={snapshot.backend_key}
      >Cancel sign-in</button>
    {:else if signIn?.state === "awaiting_return_url"}
      <p class="sign-in-step">
        Open
        <a href={signIn.verification_url ?? "#"} target="_blank" rel="noreferrer noopener"
          data-backend-sign-in-url>{signIn.verification_url}</a>
        and sign in. Your browser ends on an address Panels needs: paste it here.
      </p>
      <form
        class="sign-in-return"
        onsubmit={(event) => { event.preventDefault(); onSubmit(returnUrl.trim()); }}
        data-backend-sign-in-return={snapshot.backend_key}
      >
        <label>
          Return address
          <input type="url" required bind:value={returnUrl} data-backend-sign-in-return-url />
        </label>
        <div class="sign-in-actions">
          <button type="submit" class="sign-in-action" disabled={signInState.busy || returnUrl.trim() === ""}
            data-backend-sign-in-submit={snapshot.backend_key}>Submit</button>
          <button type="button" class="sign-in-action" disabled={signInState.busy} onclick={onCancel}
            data-backend-sign-in-cancel={snapshot.backend_key}>Cancel sign-in</button>
        </div>
      </form>
    {:else}
      {@const caveat = signInCaveat(snapshot, BACKENDS_PAGE_SURROUNDINGS)}
      {#if caveat}
        <p class="sign-in-caveat" data-backend-sign-in-caveat>{caveat}</p>
      {/if}
      <button
        class="sign-in-action"
        disabled={signInState.busy}
        onclick={onStart}
        data-backend-sign-in-start={snapshot.backend_key}
      >{signInState.busy ? "Starting…" : "Sign in"}</button>
    {/if}
    {#if outcome}
      <p class="sign-in-outcome" data-backend-sign-in-outcome>{outcome}</p>
    {/if}
    {#if signInState.error}
      <p class="error" role="alert">{signInState.error}</p>
    {/if}
  </div>
{/if}

<style>
  .sign-in { padding: var(--space-3) 0 var(--space-1); display: flex; flex-direction: column; gap: var(--space-2); align-items: flex-start; }
  .sign-in-step { margin: 0; font-size: var(--type-sm); color: var(--text-default); }
  .sign-in-step a { overflow-wrap:anywhere; }
  .sign-in-step b { font-family: var(--font-mono); color: var(--accent-text); letter-spacing: 0.06em; }
  .sign-in-caveat { margin: 0; font-size: var(--type-xs); color: var(--text-faint); max-width: 56ch; }
  .sign-in-outcome { margin: 0; font-size: var(--type-xs); color: var(--text-faint); }
  .sign-in-action { font: inherit; font-size: var(--type-xs); cursor: pointer; background: var(--surface-2); color: var(--text-default); border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-sm); padding: var(--space-1) var(--space-3); }
  .sign-in-action:disabled { cursor: default; color: var(--text-faintest); }
  .sign-in-return { display: flex; flex-direction: column; gap: var(--space-2); align-items: flex-start; width: 100%; max-width: 56ch; }
  .sign-in-return label { display: flex; flex-direction: column; gap: var(--space-1); width: 100%; font-size: var(--type-xs); color: var(--text-faint); }
  .sign-in-return input { width: 100%; font: inherit; font-size: var(--type-sm); color: var(--text-default); background: var(--surface-input); border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-sm); padding: var(--space-1) var(--space-2); }
  .sign-in-actions { display: flex; gap: var(--space-2); }
  .error { margin: 0; font-size: var(--type-small); color: var(--accent-error); }
</style>
