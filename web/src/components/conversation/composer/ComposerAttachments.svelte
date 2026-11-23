<script lang="ts">
  import { attachmentByteLabel, attachmentKindLabel, attachmentDisplayName, type ComposerAttachment } from "./attachments";
  import type { PendingVideo } from "../../../lib/conversation/videoUploads/contracts";
  let { attachments, inputDisabled, onChooseAgain, onRetry, onRemove, videoProgress }: {
    attachments: readonly ComposerAttachment[];
    inputDisabled: boolean;
    onChooseAgain: () => void;
    onRetry: (video: PendingVideo) => void;
    onRemove: (attachment: ComposerAttachment) => void;
    videoProgress: (video: PendingVideo) => string;
  } = $props();
  /** The browser owns this local preview URL until the tile leaves the DOM. */
  function localFrame(node: HTMLVideoElement, file: File) {
    const url = URL.createObjectURL(file);
    node.src = `${url}#t=0.1`;
    return { destroy: () => URL.revokeObjectURL(url) };
  }
</script>

{#if attachments.length > 0}
  <div class="chat-attachments" data-conversation-attachments aria-label="Attached and waiting to send">
    {#each attachments as attachment (`${attachment.kind}-${attachment.value.id}`)}
      {@const item = attachment.value}
      {@const videoFailed = attachment.kind === "video" && (attachment.value.phase === "failed" || attachment.value.phase === "interrupted")}
      <div
        class="chat-att"
        class:img={attachment.kind === "image"}
        class:file={attachment.kind === "file" || attachment.kind === "video"}
        class:video={attachment.kind === "video"}
        class:failed={videoFailed}
        data-chat-image-preview={attachment.kind === "image" ? "" : undefined}
        data-chat-image-name={attachment.kind === "image" ? item.fileName : undefined}
        data-chat-file-preview={attachment.kind === "file" ? "" : undefined}
        data-chat-file-name={attachment.kind === "file" ? item.fileName : undefined}
        title={`${item.fileName || "pasted image"} · ${attachmentKindLabel(item.fileName, item.mediaType)} · ${attachmentByteLabel(item.byteCount)}`}
      >
        {#if attachment.kind === "image"}
          <img src={attachment.value.previewUrl} alt="" />
        {:else if attachment.kind === "video"}
          <span class="chat-att-thumb" aria-hidden="true">
            {#if attachment.value.file}<video use:localFrame={attachment.value.file} muted playsinline preload="metadata" tabindex="-1"></video>{/if}
            <svg viewBox="0 0 24 24"><path d="m8 5 11 7-11 7z" /></svg>
          </span>
          <span class="chat-att-words"><span class="chat-att-name">{attachmentDisplayName(item.fileName)}</span>
            <span class="chat-att-meta" role="status"><span class="chat-att-type">{videoProgress(attachment.value)}</span></span>
          </span>
          {#if attachment.value.phase === "interrupted" || (attachment.value.phase === "failed" && !attachment.value.file)}
            <button type="button" class="cv-btn cv-btn--ghost cv-btn--small chat-att-again" onclick={() => onChooseAgain()}>Choose again</button>
          {/if}
          {#if attachment.value.phase === "failed" && attachment.value.file}
            <button type="button" class="cv-btn cv-btn--ghost cv-btn--small chat-att-again" onclick={() => onRetry(attachment.value)}>Retry</button>
          {/if}
          {#if attachment.value.phase === "uploading"}
            <span
              class="chat-att-progress"
              role="progressbar"
              aria-label={`Uploading ${item.fileName}`}
              aria-valuemin="0"
              aria-valuemax="100"
              aria-valuenow={Math.floor(attachment.value.receivedBytes / Math.max(1, attachment.value.byteCount) * 100)}
              style:--chat-att-progress={`${Math.min(100, attachment.value.receivedBytes / Math.max(1, attachment.value.byteCount) * 100)}%`}
            ></span>
          {/if}
        {:else}
          <span class="chat-att-kind" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8zM14 3.5V8h4.5M9 12.5h6M9 16h4" /></svg>
          </span>
          <span class="chat-att-words">
            <span class="chat-att-name">{attachmentDisplayName(item.fileName)}</span>
            <span class="chat-att-meta"><span class="chat-att-type">{attachmentKindLabel(item.fileName, item.mediaType)}</span><span class="chat-att-size">&nbsp;· {attachmentByteLabel(item.byteCount)}</span></span>
          </span>
        {/if}
        <button
          type="button"
          class="chat-att-x"
          data-chat-image-remove={attachment.kind === "image" ? "" : undefined}
          data-chat-file-remove={attachment.kind === "file" ? "" : undefined}
          aria-label={`Remove ${attachment.kind} ${item.fileName || "pasted image"}`}
          title="Remove"
          disabled={inputDisabled}
          onclick={() => onRemove(attachment)}
        ><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" /></svg></button>
      </div>
    {/each}
  </div>
{/if}

<style>
  /* Pending files and pictures: 76px tiles on the Surface, a picture filling its tile under a
     light edge and a top shade for its remove button, a file its icon, name and size. */
  .chat-attachments { padding-block-start: 0; padding-inline-end: 0; }
  .chat-att {
    height: 76px;
    border: 1px solid var(--cv-surface-edge);
    border-radius: 10px;
    background: var(--cv-surface);
  }
  .chat-att.img { width: 76px; border: 0; }
  .chat-att.img img { border-radius: 10px; object-position: left top; }
  .chat-att.img::after { content: ""; position: absolute; inset: 0; border-radius: inherit; box-shadow: inset 0 0 0 1px var(--cv-media-edge); pointer-events: none; background: linear-gradient(rgb(0 0 0 / 55%), transparent) top / 100% 28px no-repeat; }
  .chat-att.file {
    gap: 10px;
    max-width: 240px;
    padding: 0 40px 0 var(--cv-s3);
  }
  .chat-att.video { max-width: 280px; }
  .chat-att-kind { width: 32px; height: 32px; background: transparent; color: var(--cv-quiet-ink); }
  .chat-att.file > .chat-att-kind { width: 16px; }
  .chat-att-kind svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
  .chat-att-thumb { position: relative; flex: none; display: grid; place-items: center; overflow: hidden; width: 64px; height: 48px; border-radius: var(--cv-r-sm); background: var(--cv-raised); }
  .chat-att-thumb video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .chat-att-thumb svg { position: relative; width: 20px; height: 20px; padding: 4px; fill: var(--cv-primary-ink); border-radius: 50%; background: var(--cv-primary-fill); }
  .chat-att-words { gap: 0; }
  .chat-att-name { color: var(--cv-ink); font-family: var(--cv-font); font-size: var(--cv-type-control); line-height: var(--cv-leading-control); font-weight: var(--cv-weight-label); }
  .chat-att-meta { color: #b8b8b8; font-family: var(--cv-font); font-size: var(--cv-type-control); line-height: var(--cv-leading-control); font-variant-numeric: tabular-nums; }
  .chat-att.failed .chat-att-meta { color: var(--cv-danger); }
  .chat-att-again { flex: none; margin-inline-start: auto; }
  .chat-att-progress { position: absolute; inset: auto var(--cv-s2) 4px var(--cv-s2); height: 2px; border-radius: 1px; background: rgb(255 255 255 / 10%); overflow: hidden; }
  .chat-att-progress::after { content: ""; display: block; width: var(--chat-att-progress, 0%); height: 100%; background: var(--cv-t2); transition: width var(--cv-dur-base) linear; }
  .chat-att-x {
    top: 6px;
    right: 6px;
    width: 20px;
    height: 20px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: rgb(20 20 20 / 85%);
    box-shadow: inset 0 0 0 1px rgb(255 255 255 / 24%);
    color: var(--cv-ink);
    z-index: 1;
    transition: color var(--cv-dur-fast) var(--cv-ease-out), background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .chat-att-x:hover:not(:disabled) { background: rgb(36 36 36 / 92%); color: #ffffff; }
  .chat-att-x svg { display: block; width: 20px; height: 20px; padding: 5px; box-sizing: border-box; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; }
  .chat-att-x:disabled { color: var(--cv-t3); cursor: default; }
  .chat-att.file > .chat-att-x { top: 5px; right: 5px; }
</style>
