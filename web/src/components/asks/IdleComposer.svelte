<script lang="ts">
  let { placeholder = "Message the agent..." }: { placeholder?: string } = $props();
</script>

<!-- The resting composer, drawn with the conversation's own classes. It does not answer the ask. -->
<div class="chat-box" data-ask-idle-composer>
  <textarea class="chat-ta" rows="1" {placeholder} aria-label="Message" readonly tabindex="-1"></textarea>
  <div class="chat-foot">
    <div class="chat-add">
      <button type="button" class="chat-image chat-add-btn" aria-label="Add" tabindex="-1" disabled>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      </button>
    </div>
    <span class="chat-submit"></span>
  </div>
</div>
