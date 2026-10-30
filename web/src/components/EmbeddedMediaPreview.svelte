<script lang="ts">
  import { onDestroy, tick } from "svelte";
  let { href, label, kind, onerror }: {
    href: string; label: string; kind: "video" | "audio"; onerror?: () => void;
  } = $props();
  let activated = $state(false);
  let playing = $state(false);
  let elapsed = $state(0);
  let duration = $state(0);
  let player = $state<HTMLMediaElement>();
  let failure = $state("");
  let disposed = false;
  let generation = 0;
  onDestroy(() => { disposed = true; generation++; player?.pause(); });
  const seekable = $derived(Number.isFinite(duration) && duration > 0);
  $effect(() => {
    href;
    generation++;
    activated = false; playing = false; elapsed = 0; duration = 0; failure = "";
  });
  function clock(seconds: number) {
    if (!Number.isFinite(seconds) || seconds < 0) return "Unknown";
    return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  }
  function failed() { if (disposed) return; playing = false; failure = "This media cannot play in this browser."; onerror?.(); }
  async function toggle(event: MouseEvent) {
    const keyboard = event.detail === 0;
    const attempt = generation;
    const firstPlay = !activated;
    if (firstPlay) { activated = true; await tick(); }
    if (!player || disposed || attempt !== generation) return;
    if (kind === "video" && keyboard) player.focus();
    try {
      if (firstPlay || player.paused) await player.play(); else player.pause();
    } catch (error) {
      if (!disposed && attempt === generation && !(error instanceof DOMException && error.name === "AbortError")) failed();
    }
  }
  function update() {
    if (!player) return;
    elapsed = player.currentTime;
    duration = player.duration;
    playing = !player.paused && !player.ended;
  }
  function seek(next: number) {
    if (!player || !seekable) return;
    player.currentTime = Math.max(0, Math.min(duration, next));
    elapsed = player.currentTime;
  }
  function seekKey(event: KeyboardEvent) {
    if (!seekable) return;
    const values: Record<string, number> = {
      ArrowLeft: elapsed - 5, ArrowDown: elapsed - 5,
      ArrowRight: elapsed + 5, ArrowUp: elapsed + 5, Home: 0, End: duration
    };
    if (event.key in values) { event.preventDefault(); seek(values[event.key]); }
  }
  function seekPointer(event: MouseEvent) {
    const rect = event.currentTarget instanceof HTMLElement ? event.currentTarget.getBoundingClientRect() : null;
    if (rect && rect.width) seek((event.clientX - rect.left) / rect.width * duration);
  }
</script>

{#snippet playButton()}
  <button type="button" class="file-preview-play" data-media-play
    aria-label={`${playing ? "Pause" : "Play"} ${label}`} aria-pressed={playing} onclick={toggle}>
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {#if playing}<path d="M7 5h4v14H7zm6 0h4v14h-4z" />{:else}<path d="m8 5 11 7-11 7z" />{/if}
    </svg>
  </button>
{/snippet}

<div class={`embedded-media embedded-media--${kind}`} data-embedded-media={kind}>
  {#if kind === "video"}
    {#if activated}
      <!-- svelte-ignore a11y_media_has_caption -->
      <video bind:this={player} class="file-preview-video" src={href} controls autoplay tabindex="0"
        aria-label={label} onerror={failed} onloadedmetadata={update} ontimeupdate={update}
        onplay={update} onpause={update} onended={update}></video>
    {:else}
      <div class="file-preview-video-plane">{@render playButton()}</div>
    {/if}
  {:else}
    <div class="file-preview-transport">
      {@render playButton()}
      <div class="file-preview-seek" data-audio-seek role="slider" tabindex="0"
        aria-label={`Seek ${label}`} aria-valuemin="0" aria-valuemax={seekable ? duration : 0}
        aria-valuenow={elapsed} aria-valuetext={`${clock(elapsed)} of ${seekable ? clock(duration) : "unknown duration"}`}
        aria-disabled={!seekable} onkeydown={seekKey} onclick={seekPointer}>
        <span class="file-preview-track"><span class="file-preview-progress" style:width={`${seekable ? elapsed / duration * 100 : 0}%`}></span></span>
      </div>
      <span class="file-preview-time">{clock(elapsed)} / {seekable ? clock(duration) : "Unknown"}</span>
    </div>
    {#if activated}
      <audio bind:this={player} src={href} onerror={failed} onloadedmetadata={update}
        ontimeupdate={update} ondurationchange={update} onplay={update} onpause={update} onended={update}></audio>
    {/if}
  {/if}
  {#if failure}<span class="quiet-line" role="status">{label} — {failure}</span>{/if}
</div>
