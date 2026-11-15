<script lang="ts">
  // Keep production ArtifactPreview and FileDocument layout. Only their live-file
  // read boundary is replaced: this nested document is entirely synthetic and inline.
  import { MANAGED_HTML_PREVIEW_SANDBOX, prepareManagedHtmlPreviewDocument } from '../../src/lib/filePreview';
  import type { RegisterManagedArtifactFrame } from '../../src/lib/managedArtifactPress';
  let {label, reloadSignal = 0, full = false, registerArtifactFrame}: {label:string; reloadSignal?:number; full?:boolean; registerArtifactFrame?:RegisterManagedArtifactFrame; [key:string]:unknown} = $props();
  function artifactFrame(element: HTMLIFrameElement) {
    return {destroy:registerArtifactFrame?.(element)};
  }
  const sample = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{background:#151515;color:#ddd;font:16px/1.6 system-ui;margin:24px}h1{font-size:24px}button{padding:8px 12px;border:1px solid #555;border-radius:8px;background:#252525;color:#ddd}</style><h1>Sample artifact</h1><p>This local document shares the Ticket with its conversation.</p><button onclick="this.textContent='Sample control works'">Try this control</button></html>`;
  // The absolute synthetic base permits production preparation inside an opaque
  // srcdoc parent. The sample has no relative resources and causes no network read.
  let prepared = $derived(prepareManagedHtmlPreviewDocument(sample,'https://preview.invalid/sample.html',Boolean(registerArtifactFrame)));
</script>
<div class="managed-html-document" class:full>
  {#key reloadSignal}<iframe use:artifactFrame class="file-preview-document-frame" data-file-preview-html title={label} sandbox={MANAGED_HTML_PREVIEW_SANDBOX} srcdoc={prepared}></iframe>{/key}
</div>
<style>.managed-html-document {min-height:0;width:100%;height:100%;display:flex;flex-direction:column}</style>
