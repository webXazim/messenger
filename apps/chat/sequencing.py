from django.db import transaction

from apps.chat.models import Conversation


def allocate_message_sequence(conversation: Conversation) -> tuple[Conversation, int]:
    """Lock a conversation and reserve its next durable message sequence.

    Callers must already be inside transaction.atomic(). The lock makes sequence
    allocation deterministic across Django, support-chat, and future SQLx writers.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("Message sequence allocation requires transaction.atomic().")
    locked = Conversation.objects.select_for_update().get(pk=conversation.pk)
    sequence = int(locked.next_message_sequence or 0) + 1
    locked.next_message_sequence = sequence
    locked.save(update_fields=["next_message_sequence", "updated_at"])
    return locked, sequence
