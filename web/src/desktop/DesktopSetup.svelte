<script lang="ts">
  import { onMount, onDestroy, tick } from "svelte";
  import { desktopBridge } from "./bridge";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import installation from "../../../ops/mac-home/installation.md?raw";

  type Step = "where" | "have" | "install" | "connect";
  let step = $state<Step>("where");
  let reverse = $state(false);
  let place = $state<"local" | "remote" | null>(null);
  let have = $state<"yes" | "no" | null>(null);
  let stored = $state<string | null>(null);
  let local = $state("http://127.0.0.1:8767");
  let address = $state("");
  let problem = $state("");
  let busy = $state(false);
  let copied = $state(false);
  let copyProblem = $state("");
  let guideOpen = $state(false);
  let input = $state<HTMLInputElement>();
  let body = $state<HTMLDivElement>();
  let guideHost = $state<HTMLDivElement>();
  let copyTimer: ReturnType<typeof setTimeout> | undefined;
  let guideObserver: MutationObserver | undefined;
  const command = installation.match(/```sh\s*\n([\s\S]*?)\n```/)?.[1].trim() ?? "";
  // The complete canonical guide ships offline. Supporting pages open in the browser.
  const guide = installation.replace(/\]\((\.\.[^)]*)\)/g, (_, relative: string) =>
    `](${new URL(relative, "https://github.com/khushal-jagota/headroom/blob/main/ops/mac-home/installation.md").href})`);
  const canContinue = $derived(!busy && (step === "where" ? place !== null : step === "have" ? have !== null : step === "connect" ? address.trim().length > 0 : true));

  onMount(async () => {
    [stored, local] = await Promise.all([desktopBridge().storedServerAddress(), desktopBridge().localServerAddress()]);
  });
  onDestroy(() => { clearTimeout(copyTimer); guideObserver?.disconnect(); });

  function choosePlace(value: "local" | "remote"): void {
    if (place !== value) have = null;
    place = value;
  }
  async function show(value: Step, back = false): Promise<void> {
    reverse = back;
    step = value;
    problem = "";
    if (value === "connect" && !back) address = place === "local" ? local : stored?.startsWith("https:") ? stored : "";
    await tick();
    body?.scrollTo({ top: 0 });
    if (value === "connect") { input?.focus(); input?.select(); }
    else (body?.querySelector<HTMLButtonElement>('[role="radio"][aria-checked="true"]') ?? body?.querySelector<HTMLButtonElement>('[role="radio"], .disclose'))?.focus({ preventScroll: true });
  }
  function forward(): void {
    if (!canContinue) return;
    if (step === "where") void show("have");
    else if (step === "have") void show(have === "yes" ? "connect" : "install");
    else if (step === "install") void show("connect");
    else void connect();
  }
  function back(): void {
    if (busy) return;
    void show(step === "have" ? "where" : step === "install" ? "have" : have === "no" ? "install" : "have", true);
  }
  function keyboard(event: KeyboardEvent): void {
    if (busy) return;
    if (event.key === "Escape" && stored !== null) { event.preventDefault(); void desktopBridge().returnToServer(); }
    if (event.key === "Enter" && !(event.target as HTMLElement).closest('form, .copy, .disclose, .inline-link, .assist-bar')) {
      event.preventDefault(); forward();
    }
  }
  function tileKeys(event: KeyboardEvent, index: number): void {
    if (!["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    const next = index === 0 ? 1 : 0;
    if (step === "where") choosePlace(next === 0 ? "local" : "remote");
    else have = next === 0 ? "yes" : "no";
    (event.currentTarget as HTMLElement).parentElement?.querySelectorAll<HTMLButtonElement>('[role="radio"]')[next]?.focus();
  }
  async function connect(): Promise<void> {
    if (busy || !address.trim()) return;
    busy = true;
    problem = "";
    try {
      if (place === "remote" && !/^https:\/\//i.test(address.trim())) {
        problem = "Enter the Tailscale HTTPS address, including https://.";
      } else {
        const result = await desktopBridge().useServerAddress(address);
        if (!result.ok) problem = result.problem;
      }
    } catch { problem = "The app could not connect. Try again."; }
    finally {
      busy = false;
      await tick();
      if (problem) { input?.focus(); input?.select(); }
    }
  }
  async function copy(): Promise<void> {
    copyProblem = "";
    try {
      await navigator.clipboard.writeText(command);
      copied = true;
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => copied = false, 1600);
    } catch { copyProblem = "The command could not be copied. Select it and copy it."; }
  }
  async function openGuideAtStepFour(): Promise<void> {
    guideOpen = true;
    await tick();
    guideObserver?.disconnect();
    const scroll = (): boolean => {
      const heading = Array.from(guideHost?.querySelectorAll<HTMLElement>("h2, h3") ?? [])
        .find(node => node.textContent?.includes("4. Reach the home from a different device"));
      if (!heading) return false;
      heading.scrollIntoView({ block: "start" });
      heading.classList.remove("flash");
      void heading.offsetWidth;
      heading.classList.add("flash");
      return true;
    };
    if (!scroll() && guideHost) {
      guideObserver = new MutationObserver(() => { if (scroll()) guideObserver?.disconnect(); });
      guideObserver.observe(guideHost, { subtree: true, childList: true });
    }
  }
</script>

<svelte:window onkeydown={keyboard} />

{#snippet tickMark()}
  <i class="tick" aria-hidden="true"><svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6.5l2.5 2.5 4.5-5.5" /></svg></i>
{/snippet}
<main class="setup" data-desktop-setup>
  <div class="window-top-row" aria-hidden="true"></div>
  <div class="assist-body" bind:this={body}>
    <div class="assist-col">
      {#key step}
        <section class="step" class:reverse aria-labelledby="setup-title" data-step={step}>
          {#if step === "where"}
            <img class="mark" src="/static/icon-192.png" alt="" />
            <h1 id="setup-title">{stored !== null ? "Change server" : "Where does Panels run?"}</h1>
            <p class="lead">{#if stored !== null}Connected to <b>{stored}</b>{:else}This app opens a Panels server. It never runs one.{/if}</p>
            <div class="tiles" role="radiogroup" aria-labelledby="setup-title">
              {#each ["local", "remote"] as value, index}
                <button type="button" class="tile" role="radio" aria-checked={place === value} tabindex={place === value || (place === null && index === 0) ? 0 : -1}
                  onclick={() => choosePlace(value as "local" | "remote")} ondblclick={() => { choosePlace(value as "local" | "remote"); forward(); }} onkeydown={event => tileKeys(event, index)}>
                  <svg class="glyph" aria-hidden="true" viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round">
                    {#if value === "local"}<rect x="5" y="6" width="18" height="12.5" rx="1.75" /><path d="M2.5 22h23" />{:else}<rect x="4" y="5" width="20" height="7" rx="1.75" /><rect x="4" y="16" width="20" height="7" rx="1.75" /><path d="M8.5 8.5h.01M8.5 19.5h.01" stroke-width="2.2" stroke-linecap="round" />{/if}
                  </svg>
                  {@render tickMark()}
                  <strong>{value === "local" ? "On this Mac" : "On another machine"}</strong><span>{value === "local" ? "Keep this Mac on while your agents work." : "Connect through Tailscale, for example to a VPS."}</span>
                </button>
              {/each}
            </div>
          {:else if step === "have"}
            <h1 id="setup-title">Is Panels already on {place === "local" ? "this Mac" : "that machine"}?</h1>
            <div class="tiles" role="radiogroup" aria-labelledby="setup-title">
              {#each ["yes", "no"] as value, index}
                <button type="button" class="tile" role="radio" aria-checked={have === value} tabindex={have === value || (have === null && index === 0) ? 0 : -1}
                  onclick={() => have = value as "yes" | "no"} ondblclick={() => { have = value as "yes" | "no"; forward(); }} onkeydown={event => tileKeys(event, index)}>
                  <svg class="glyph" aria-hidden="true" viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    {#if value === "yes"}<path d="M12 16a4.5 4.5 0 006.4 0l3.6-3.6a4.5 4.5 0 00-6.4-6.4l-1.2 1.2M16 12a4.5 4.5 0 00-6.4 0L6 15.6a4.5 4.5 0 006.4 6.4l1.2-1.2" />{:else}<path d="M14 4.5v13M8.5 12l5.5 5.5 5.5-5.5M5 23h18" />{/if}
                  </svg>
                  {@render tickMark()}
                  <strong>{value === "yes" ? "Yes, connect to it" : "No, install it"}</strong><span>{value === "yes" ? "You need its address." : "One command installs the server and machine code."}</span>
                </button>
              {/each}
            </div>
          {:else if step === "install"}
            <h1 id="setup-title">Install Panels on {place === "local" ? "this Mac" : "the other machine"}</h1>
            <p class="hint">{place === "local" ? "Sign in to the GitHub CLI, then run this in Terminal:" : "On that machine, sign in to the GitHub CLI, then run this:"}</p>
            <div class="command"><span>{command}</span><button type="button" class="button button--ghost copy" onclick={copy}><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="5.5" y="5.5" width="7.5" height="7.5" rx="1.5" /><path d="M3 10.5V4a1 1 0 011-1h6.5" /></svg>{copied ? "Copied" : "Copy"}</button></div>
            {#if copyProblem}<p class="problem" role="alert">{copyProblem}</p>{/if}
            <p class="hint">{#if place === "local"}The installer prints the server’s address when it is ready.{:else}Then publish it with Tailscale. Follow <button type="button" class="inline-link" onclick={openGuideAtStepFour}>step 4 of the guide</button> before you continue.{/if}</p>
            <button type="button" class="button button--ghost disclose" aria-expanded={guideOpen} aria-controls="guide" onclick={() => guideOpen = !guideOpen}>
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M6 4l4 4-4 4" /></svg>Installation guide
            </button>
            <div id="guide" class="guide" hidden={!guideOpen} bind:this={guideHost}><MarkdownBlock text={guide} /></div>
          {:else}
            <h1 id="setup-title">Connect to your server</h1>
            <p class="lead">{#if have === "no"}{#if place === "local"}Use the address the installer printed.{:else}Use the address from <code>tailscale serve status</code>.{/if}{:else}{place === "local" ? "A server on this Mac listens here unless you chose another port." : "Use the server’s Tailscale HTTPS address."}{/if}</p>
            <form id="address-form" onsubmit={event => { event.preventDefault(); void connect(); }}>
              <label for="address">{place === "local" ? "Local server address" : "Tailscale HTTPS address"}</label>
              <input id="address" bind:this={input} bind:value={address} oninput={() => problem = ""} placeholder={place === "local" ? local : "https://machine.tailnet.ts.net"} spellcheck="false" autocomplete="url" disabled={busy} aria-invalid={problem ? "true" : undefined} aria-describedby={problem ? "problem" : undefined} />
              <p id="problem" class="problem" role="alert">{#if problem}<svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="6.25" /><path d="M8 4.75v3.75M8 11h.01" /></svg><span>{problem}</span>{/if}</p>
            </form>
          {/if}
        </section>
      {/key}
    </div>
  </div>
  <footer class="assist-bar">
    {#if stored !== null}<button type="button" class="button button--ghost button--main" disabled={busy} onclick={() => desktopBridge().returnToServer()}>Cancel</button>{/if}
    <span class="spacer"></span>
    {#if step !== "where"}<button type="button" class="button button--secondary button--main" disabled={busy} onclick={back}>Back</button>{/if}
    <button id={step === "connect" ? "connect" : undefined} type="button" class="button button--primary button--main" disabled={!canContinue} onclick={forward}>{#if busy}<i class="spinner" aria-hidden="true"></i>{/if}{busy ? "Checking…" : step === "connect" ? "Connect" : "Continue"}</button>
  </footer>
</main>

<style>
  .setup { position: fixed; inset: 0; container-type: size; background: var(--surface-1); color: var(--tone-primary); }
  .window-top-row { position: absolute; z-index: 1; inset: 0 0 auto; height: var(--window-top-row); -webkit-app-region: drag; }
  .assist-body { position: absolute; inset: var(--window-top-row) 0 var(--setup-bar-height); overflow: auto; scrollbar-gutter: stable both-edges; mask-image: linear-gradient(to bottom, transparent, #000 var(--space-4), #000 calc(100% - var(--space-5)), transparent); }
  .assist-col { width: min(var(--setup-column-width), calc(100% - 2 * var(--space-6))); margin: 0 auto; padding: clamp(var(--space-6), 9cqh, var(--setup-top-max)) 0 var(--space-6); }
  .assist-bar { position: absolute; inset: auto 0 0; height: var(--setup-bar-height); display: flex; align-items: center; gap: var(--space-2); padding: 0 var(--space-6) var(--space-1); }
  .spacer { flex: 1; }
  .assist-bar .button { height: var(--button-main-height); min-width: var(--setup-button-min-width); }
  .step { animation: step-in var(--motion-enter) var(--motion-ease); }
  .step.reverse { animation-name: step-back; }
  @keyframes step-in { from { opacity: 0; transform: translateX(var(--space-4)); } }
  @keyframes step-back { from { opacity: 0; transform: translateX(calc(-1 * var(--space-4))); } }
  .mark { display: block; width: var(--setup-icon-size); height: var(--setup-icon-size); border-radius: var(--setup-icon-radius); box-shadow: var(--setup-icon-shadow); margin-bottom: var(--space-5); }
  h1 { font-size: var(--type-page-title); font-weight: var(--weight-page-title); color: var(--tone-strong); margin: 0; letter-spacing: -.01em; line-height: 1.25; }
  .lead { color: var(--tone-secondary); margin: var(--space-2) 0 0; }
  .lead b { font-weight: var(--weight-small); color: var(--tone-primary); overflow-wrap: anywhere; }
  .tiles { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); margin-top: var(--space-6); }
  .tile { position: relative; display: flex; flex-direction: column; align-items: flex-start; gap: var(--space-1); padding: var(--space-5); min-height: var(--setup-tile-height); border: 0; border-radius: var(--radius-lg); background: var(--surface-2); color: var(--tone-primary); text-align: left; cursor: pointer; transition: background-color var(--motion-fast) var(--motion-ease); }
  .tile:hover { background: var(--surface-hover); }
  .tile[aria-checked="true"] { background: var(--surface-selected); }
  .glyph { width: var(--setup-glyph-size); height: var(--setup-glyph-size); color: var(--tone-secondary); margin-bottom: var(--space-5); }
  .tile[aria-checked="true"] .glyph { color: var(--tone-strong); }
  .tile strong { font-size: var(--type-page-section); font-weight: var(--weight-page-section); color: var(--tone-strong); }
  .tile > span { font-size: var(--type-small); font-weight: var(--weight-small); color: var(--tone-tertiary); line-height: 1.45; }
  .tick { position: absolute; top: var(--space-4); right: var(--space-4); width: var(--setup-tick-size); height: var(--setup-tick-size); border-radius: var(--radius-pill); background: var(--button-primary-fill); color: var(--button-primary-ink); display: grid; place-items: center; opacity: 0; transform: scale(.6); transition: opacity var(--motion-base) var(--motion-ease), transform var(--motion-base) var(--motion-ease); }
  .tick svg { width: var(--setup-tick-glyph-size); height: var(--setup-tick-glyph-size); }
  .tile[aria-checked="true"] .tick { opacity: 1; transform: none; }
  form { margin-top: var(--space-6); }
  label { display: block; color: var(--tone-tertiary); font-size: var(--type-small); font-weight: var(--weight-small); margin-bottom: var(--space-2); }
  input { box-sizing: border-box; width: 100%; height: var(--button-main-height); padding: 0 var(--space-3); background: var(--surface-input); color: var(--tone-strong); border: 0; border-radius: var(--radius-md); font: inherit; font-size: var(--type-input); }
  input::placeholder { color: var(--tone-faint); font-style: italic; }
  input:focus { outline: var(--setup-field-focus-width) solid var(--focus-ring); outline-offset: 0; }
  input[aria-invalid="true"] { outline: var(--border-hairline) solid var(--accent-error); }
  .problem { display: flex; gap: var(--space-2); align-items: flex-start; color: var(--accent-error); font-size: var(--type-small); font-weight: var(--weight-small); margin: var(--space-2) 0 0; min-height: var(--setup-problem-height); overflow-wrap: anywhere; }
  .problem svg { width: var(--type-body); height: var(--type-body); flex: none; margin-top: var(--border-strong); }
  .problem:empty { visibility: hidden; }
  .hint { font-size: var(--type-small); font-weight: var(--weight-small); color: var(--tone-tertiary); margin: var(--space-5) 0 var(--space-2); }
  .hint:first-of-type { margin-top: var(--space-6); }
  .command { position: relative; background: var(--surface-recessed); box-shadow: var(--setup-inset-shadow); border-radius: var(--radius-md); padding: var(--space-3) var(--setup-command-pad) var(--space-3) var(--space-4); font-family: var(--font-mono); font-size: var(--type-small); color: var(--tone-primary); line-height: 1.6; overflow-wrap: anywhere; }
  .copy { position: absolute; top: var(--setup-copy-inset); right: var(--setup-copy-inset); }
  .inline-link { font: inherit; color: var(--accent-text); background: none; border: 0; padding: 0; cursor: pointer; }
  .inline-link:hover { text-decoration: underline; }
  .lead code { font-family: var(--font-mono); font-size: .92em; color: var(--tone-primary); }
  .disclose { margin-left: calc(-1 * var(--button-pad-x)); margin-top: var(--space-3); }
  .disclose svg { transition: transform var(--motion-base) var(--motion-ease); }
  .disclose[aria-expanded="true"] svg { transform: rotate(90deg); }
  .guide { margin-top: var(--space-2); background: var(--surface-recessed); box-shadow: var(--setup-inset-shadow); border-radius: var(--radius-md); padding: var(--space-4) var(--space-5); max-height: var(--setup-guide-height); overflow: auto; }
  .guide[hidden] { display: none; }
  .guide :global(.flash) { animation: flash var(--setup-flash-duration) var(--motion-ease); border-radius: var(--radius-sm); }
  @keyframes flash { 0%, 40% { background: var(--accent-surface); } 100% { background: transparent; } }
  .spinner { width: var(--type-body); height: var(--type-body); border: var(--border-strong) solid currentColor; border-right-color: transparent; border-radius: var(--radius-pill); animation: spin var(--motion-loop-spin) linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .step, .spinner, .guide :global(.flash) { animation: none; } .tile, .tick, .disclose svg { transition: none; } }
</style>
