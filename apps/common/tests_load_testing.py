from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts.models import UserSession
from apps.chat.models import Conversation, ConversationParticipant


@override_settings(CENTRAL_AUTH_ENABLED=False)
class RealtimeLoadTestFixtureTests(TestCase):
    def test_prepare_and_cleanup_are_scoped_to_run_id(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ALLOW_LOAD_TEST_DATA": "true"}):
            output = Path(directory) / "users.json"
            call_command(
                "prepare_realtime_load_test",
                users=2,
                run_id="test-run",
                output=str(output),
                token_hours=1,
                confirm=True,
            )
            payload = json.loads(output.read_text())
            self.assertEqual(payload["user_count"], 2)
            self.assertEqual(len(payload["users"]), 2)
            self.assertEqual(get_user_model().objects.filter(username__startswith="loadtest_test-run_").count(), 2)
            self.assertEqual(Conversation.objects.filter(direct_key__startswith="loadtest:test-run:").count(), 1)
            self.assertEqual(ConversationParticipant.objects.filter(conversation__direct_key__startswith="loadtest:test-run:").count(), 2)
            self.assertEqual(UserSession.objects.filter(user__username__startswith="loadtest_test-run_").count(), 2)

            call_command(
                "cleanup_realtime_load_test",
                run_id="test-run",
                credential_file=str(output),
                confirm=True,
            )
            self.assertFalse(output.exists())
            self.assertFalse(get_user_model().objects.filter(username__startswith="loadtest_test-run_").exists())
            self.assertFalse(Conversation.objects.filter(direct_key__startswith="loadtest:test-run:").exists())
