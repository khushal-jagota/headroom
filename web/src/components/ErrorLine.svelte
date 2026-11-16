<script lang="ts">
  import { errorCode, errorMessage } from "../lib/ui";

  let { error }: { error: unknown } = $props();
</script>

<div class="error-line" data-error-code={errorCode(error)}>
  <span class="error-message">{errorMessage(error)}</span>
</div>
