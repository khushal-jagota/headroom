<script lang="ts">import MachineSymbol from "./MachineSymbol.svelte";</script>
<div class="machine-key" aria-label="Machine key"><span><MachineSymbol locale="macos" /> Mac</span><span><MachineSymbol locale="vps" /> VPS</span></div>
<style>.machine-key,.machine-key span { display:flex; align-items:center; gap:var(--space-2); }.machine-key { gap:var(--space-4); font:var(--type-sm) var(--font-ui); color:var(--text-faint); padding-block:var(--space-2); }</style>
