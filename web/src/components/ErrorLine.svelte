<script lang="ts">
  import { errorCode, errorMessage } from "../lib/ui";

  let { error }: { error: unknown } = $props();
</script>

<div class="error-line">
  <span class="error-code">{errorCode(error)}</span>
  <span class="error-message">{errorMessage(error)}</span>
</div>
