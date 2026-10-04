from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.events import (
    ConversationEventKind,
    PromptEventPayload,
    conversation_event_payload_from_canonical_json,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.message_content import text_message_content


def test_reviewed_receipt_pointer_round_trips_without_inventing_sender_identity() -> None:
    receipt = PromptEventPayload(
        content=text_message_content("reviewed delivery"),
        sender_label="supervisor",
        mode=PromptDeliveryMode.queue,
        reconciles_sequence=17,
    )
    encoded = conversation_event_payload_to_canonical_json(receipt)
    assert '"reconciles_sequence":17' in encoded
    assert "sender_message_id" not in encoded
    assert (
        conversation_event_payload_from_canonical_json(ConversationEventKind.prompt, encoded)
        == receipt
    )
