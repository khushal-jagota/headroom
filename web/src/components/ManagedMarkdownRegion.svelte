<script lang="ts">
  import SkeletonRegion from "./SkeletonRegion.svelte";

  // The surface owns the contents of this host. Svelte owns the placeholder and content wrappers.
  // The action runs after insertion, so visibility observers find the scroll root.
  let renderContent = $state<((host: HTMLElement) => void) | null>(null);
  export function reveal(render: (host: HTMLElement) => void): void {
    renderContent = render;
  }
  function populate(host: HTMLElement): void {
    renderContent?.(host);
  }
</script>

<SkeletonRegion shape="document" loading={renderContent === null} loadingText="Loading text...">
  <div use:populate></div>
</SkeletonRegion>
