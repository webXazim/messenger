from datetime import timedelta
from uuid import uuid4

from django.test import SimpleTestCase
from django.utils import timezone

from apps.support.api.cursors import (
    InvalidSupportInboxCursor,
    decode_support_inbox_cursor,
    encode_support_inbox_cursor,
)


class SupportInboxCursorTests(SimpleTestCase):
    def test_round_trip(self):
        ordered_at = timezone.now().replace(microsecond=0)
        conversation_id = uuid4()
        encoded = encode_support_inbox_cursor(
            ordered_at=ordered_at,
            conversation_id=conversation_id,
        )
        decoded = decode_support_inbox_cursor(encoded)
        self.assertEqual(decoded.ordered_at, ordered_at)
        self.assertEqual(decoded.conversation_id, conversation_id)

    def test_rejects_tampering(self):
        encoded = encode_support_inbox_cursor(
            ordered_at=timezone.now(),
            conversation_id=uuid4(),
        )
        with self.assertRaises(InvalidSupportInboxCursor):
            decode_support_inbox_cursor(f"{encoded}tampered")

    def test_rejects_expired_cursor(self):
        encoded = encode_support_inbox_cursor(
            ordered_at=timezone.now() - timedelta(days=2),
            conversation_id=uuid4(),
        )
        # Signing age is based on issuance, so a negative maximum age gives a
        # deterministic expiry assertion without sleeping.
        with self.assertRaises(InvalidSupportInboxCursor):
            decode_support_inbox_cursor(encoded, max_age_seconds=-1)
