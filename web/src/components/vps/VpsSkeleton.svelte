<script lang="ts">
  let { shape = 'line' }: { shape?: 'line' | 'value' | 'detail' | 'trend' } = $props();
</script>
<span class="vps-block vps-block--{shape}" aria-hidden="true"></span>
