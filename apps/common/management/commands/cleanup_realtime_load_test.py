from __future__ import annotations

import os
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.chat.models import Conversation

RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")


class Command(BaseCommand):
    help = "Delete temporary users and cascaded data created by prepare_realtime_load_test."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--credential-file", default="")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        if str(os.getenv("ALLOW_LOAD_TEST_DATA", "")).strip().lower() not in {"1", "true", "yes", "on"}:
            raise CommandError("Set ALLOW_LOAD_TEST_DATA=true for the cleanup operation.")
        if not options["confirm"]:
            raise CommandError("Pass --confirm to delete the temporary load-test users and cascaded data.")
        run_id = str(options["run_id"]).strip().lower()
        if not RUN_ID_RE.fullmatch(run_id):
            raise CommandError("Invalid --run-id.")

        User = get_user_model()
        prefix = f"loadtest_{run_id}_"
        queryset = User.objects.filter(username__startswith=prefix)
        user_count = queryset.count()
        with transaction.atomic():
            conversation_total, conversation_details = Conversation.objects.filter(
                direct_key__startswith=f"loadtest:{run_id}:"
            ).delete()
            deleted_total, details = queryset.delete()

        credential_file = str(options.get("credential_file") or "").strip()
        if credential_file:
            path = Path(credential_file).expanduser().resolve()
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                self.stdout.write(self.style.WARNING(f"Could not remove credential file {path}: {error}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Removed {user_count} load-test users, {conversation_total} conversation-related rows, "
                f"and {deleted_total} user-related rows."
            )
        )
        if conversation_details:
            self.stdout.write(str(conversation_details))
        if details:
            self.stdout.write(str(details))
