<script lang="ts">
  import FilePreview from "../src/components/FilePreview.svelte";
</script>

<FilePreview
  target={{ kind: "external-link", href: "./missing-artifact.png", label: "Missing" }}
/>
