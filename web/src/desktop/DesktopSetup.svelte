<script lang="ts">
  import { onMount } from "svelte";
  import { desktopBridge } from "./bridge";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import installation from "../../../ops/mac-home/installation.md?raw";

  let place = $state<"local" | "remote" | null>(null);
  let installing = $state(false);
  let stored = $state<string | null>(null);
  let local = $state("http://127.0.0.1:8767");
  let address = $state("");
  let problem = $state("");
  let busy = $state(false);
  // The complete canonical guide ships offline. Supporting pages open in the browser.
  const guide = installation.replace(/\]\((\.\.[^)]*)\)/g, (_, relative: string) =>
    `](${new URL(relative, "https://github.com/khushal-jagota/headroom/blob/main/ops/mac-home/installation.md").href})`);

  onMount(async () => {
    [stored, local] = await Promise.all([desktopBridge().storedServerAddress(), desktopBridge().localServerAddress()]);
  });

  function choose(value: "local" | "remote"): void {
    place = value;
    address = value === "local" ? local : (stored?.startsWith("https:") ? stored : "") ?? "";
    problem = "";
  }

  async function connect(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    busy = true;
    problem = "";
    try {
      if (place === "remote" && !/^https:\/\//i.test(address.trim())) {
        problem = "Enter the Tailscale HTTPS address, including https://.";
        return;
      }
      const result = await desktopBridge().useServerAddress(address);
      if (!result.ok) problem = result.problem;
    } catch {
      problem = "The app could not connect. Try again.";
    } finally { busy = false; }
  }
</script>

<main class="setup" data-desktop-setup>
  <div class="identity">Panels <span>/ {stored ? "Change server" : "First setup"}</span></div>
  <section class="setup-content">
    <div class="step">01 — THE SERVER</div>
    <h1>Where will Panels live?</h1>
    <p class="intro">Your work stays on one server. This app is your window into it.</p>
    <div class="places" role="group" aria-label="Where the server lives">
      <button type="button" class:selected={place === "local"} disabled={busy} aria-pressed={place === "local"} onclick={() => choose("local")}>
        <span class="place-number">01</span><strong>On this Mac</strong><span>Keep this Mac on while your agents work.</span>
      </button>
      <button type="button" class:selected={place === "remote"} disabled={busy} aria-pressed={place === "remote"} onclick={() => choose("remote")}>
        <span class="place-number">02</span><strong>On another machine</strong><span>Connect through Tailscale, for example to a VPS.</span>
      </button>
    </div>
    {#if place}
      <section class="connection" aria-label="Connect to the server">
        <div class="connection-heading"><h2>{place === "local" ? "A server on this Mac" : "A server on your network"}</h2>
          <button type="button" class="text-action" onclick={() => installing = !installing}>{installing ? "I already have a server" : "I need a new server"}</button>
        </div>
        {#if installing}
          <p>Run the existing installer on {place === "local" ? "this Mac" : "the machine that will hold the server"}. It installs the server and machine code together.</p>
          <p>{place === "remote" ? "Complete the remote-access steps before you connect here." : "The server runs separately from this app. The installer prints its address."}</p>
          <details class="guide"><summary>Installation and first-setup steps</summary><MarkdownBlock text={guide} /></details>
        {/if}
        <form id="address-form" onsubmit={connect}>
          <label for="address">{place === "local" ? "Local server address" : "Tailscale HTTPS address"}</label>
          <div class="address-row"><input id="address" bind:value={address} placeholder={place === "local" ? local : "https://machine.tailnet.ts.net"} spellcheck="false" autocomplete="url" required disabled={busy} />
          <button id="connect" type="submit" disabled={busy}>{busy ? "Checking…" : "Connect"}</button></div>
          {#if problem}<p id="problem" role="alert">{problem}</p>{/if}
        </form>
      </section>
    {/if}
    {#if stored}<button class="text-action back" type="button" disabled={busy} onclick={() => desktopBridge().returnToServer()}>Return to current server</button>{/if}
  </section>
</main>

<style>
  .setup { min-height: 100dvh; background: var(--surface-1); color: var(--text-strong); padding: var(--space-6); overflow: auto; }
  .identity { font-weight: 600; font-size: var(--type-body); }
  .identity span { color: var(--text-muted); font-weight: 400; margin-left: var(--space-2); }
  .setup-content { max-width: 720px; margin: var(--space-6) auto; padding-block: var(--space-6); }
  .step { font-size: var(--type-small); color: var(--accent-text); letter-spacing: .12em; margin-bottom: var(--space-3); }
  h1 { font-size: var(--type-page-title); font-weight: 550; margin: 0 0 var(--space-3); }
  .intro { color: var(--text-muted); margin: 0 0 var(--space-6); }
  .places { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-3); }
  .places button { display: flex; flex-direction: column; gap: var(--space-3); align-items: start; text-align: left; padding: var(--space-5); border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-md); background: var(--surface-2); color: inherit; cursor: pointer; }
  .places button:hover { background: var(--surface-hover); }
  .places button.selected { border-color: var(--accent-bright); background: var(--accent-surface); }
  .place-number { font-size: var(--type-small); color: var(--text-muted); }
  .places strong { font-size: var(--type-body); font-weight: 550; }
  .places button > span:last-child { font-size: var(--type-small); color: var(--text-muted); }
  .connection { margin-top: var(--space-6); }
  .connection-heading { display: flex; flex-wrap: wrap; justify-content: space-between; gap: var(--space-3); align-items: baseline; }
  h2 { font-size: var(--type-body); font-weight: 550; margin: 0; }
  .connection p { color: var(--text-muted); }
  .text-action { background: transparent; border: 0; padding: 0; color: var(--accent-text); cursor: pointer; font: inherit; font-size: var(--type-small); }
  .text-action:hover { text-decoration: underline; }
  .guide { background: var(--surface-2); border-radius: var(--radius-md); padding: var(--space-4); margin-block: var(--space-4); }
  summary { cursor: pointer; color: var(--text-strong); }
  .guide :global(.markdown-block) { margin-top: var(--space-4); }
  form { margin-top: var(--space-4); }
  label { display: block; font-size: var(--type-small); color: var(--text-muted); margin-bottom: var(--space-2); }
  .address-row { display: flex; gap: var(--space-3); }
  input { flex: 1; min-width: 0; padding: var(--space-3); background: var(--surface-2); color: var(--text-strong); border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-sm); font: inherit; }
  #connect { padding: var(--space-3) var(--space-5); background: var(--accent-bright); color: var(--surface-1); border: 0; border-radius: var(--radius-sm); font: inherit; font-weight: 600; cursor: pointer; }
  #problem { color: var(--accent-error); font-size: var(--type-small); }
  .back { margin-top: var(--space-6); }
  button:focus-visible, input:focus-visible, summary:focus-visible { outline: var(--focus-width) solid var(--focus-ring); outline-offset: var(--space-1); }
  button:disabled { opacity: .5; cursor: wait; }
  @media (max-width: 540px) { .setup { padding: var(--space-4); } .places { grid-template-columns: 1fr; } .setup-content { padding-block: 0; } }
</style>
