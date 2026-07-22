from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from apps.chat.api.serializers import MessageSerializer
from apps.chat.models import Conversation, ConversationParticipant, Message
from apps.chat.services import restore_message, retry_message, soft_delete_message


User = get_user_model()


class SenderMessageRestoreTests(TestCase):
    def setUp(self):
        self.sender = User.objects.create_user(
            username="restore_sender",
            email="restore-sender@example.com",
            password="StrongPass123!",
        )
        self.member = User.objects.create_user(
            username="restore_member",
            email="restore-member@example.com",
            password="StrongPass123!",
        )
        self.conversation = Conversation.objects.create(
            type=Conversation.ConversationType.DIRECT,
            created_by=self.sender,
        )
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.sender,
            role=ConversationParticipant.Role.OWNER,
        )
        ConversationParticipant.objects.create(
            conversation=self.conversation,
            user=self.member,
        )

    def test_sender_delete_preserves_text_and_can_be_restored(self):
        message = Message.objects.create(
            conversation=self.conversation,
            sender=self.sender,
            text="Restore this message",
        )

        deleted = soft_delete_message(self.sender, message)
        self.assertTrue(deleted.is_deleted)
        self.assertEqual(deleted.text, "")
        self.assertEqual(deleted.deleted_text_backup, "Restore this message")
        self.assertEqual(deleted.deletion_source, Message.DeletionSource.SENDER)

        restored = restore_message(self.sender, deleted)
        self.assertFalse(restored.is_deleted)
        self.assertEqual(restored.text, "Restore this message")
        self.assertEqual(restored.deleted_text_backup, "")
        self.assertEqual(restored.deletion_source, "")


    def test_deleted_message_serializer_hides_private_content(self):
        message = Message.objects.create(
            conversation=self.conversation,
            sender=self.sender,
            text="Private content",
            metadata={
                "raw_text": "Private content",
                "entities": [{"type": "bold", "offset": 0, "length": 7}],
                "links": ["https://example.com/private"],
            },
        )
        deleted = soft_delete_message(self.sender, message)
        request = APIRequestFactory().get("/")
        request.user = self.sender

        payload = MessageSerializer(deleted, context={"request": request}).data

        self.assertEqual(payload["text"], "")
        self.assertEqual(payload["metadata"], {})
        self.assertEqual(payload["attachments"], [])
        self.assertEqual(payload["edit_history"], [])
        self.assertEqual(payload["entities"], [])
        self.assertEqual(payload["links"], [])
        self.assertTrue(payload["can_restore"])

    def test_moderation_and_legacy_deletions_cannot_be_sender_restored(self):
        for source in (Message.DeletionSource.MODERATION, ""):
            message = Message.objects.create(
                conversation=self.conversation,
                sender=self.sender,
                text="",
                is_deleted=True,
                deleted_text_backup="Protected text",
                deletion_source=source,
            )
            with self.assertRaises(PermissionDenied):
                restore_message(self.sender, message)


    def test_retry_is_idempotent_after_success(self):
        message = Message.objects.create(
            conversation=self.conversation,
            sender=self.sender,
            text="Retry me",
            delivery_status=Message.DeliveryStatus.FAILED,
            failed_reason="temporary",
        )
        first = retry_message(self.sender, message)
        second = retry_message(self.sender, first)
        self.assertEqual(first.retry_count, 1)
        self.assertEqual(second.retry_count, 1)
        self.assertEqual(second.delivery_status, Message.DeliveryStatus.SENT)

    def test_other_participant_cannot_delete_or_restore_message(self):
        message = Message.objects.create(
            conversation=self.conversation,
            sender=self.sender,
            text="Owner only",
        )
        with self.assertRaises(PermissionDenied):
            soft_delete_message(self.member, message)

        deleted = soft_delete_message(self.sender, message)
        with self.assertRaises(PermissionDenied):
            restore_message(self.member, deleted)
