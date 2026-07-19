from __future__ import annotations

import json
import os
import re
import uuid
from datetime import timedelta, timezone as datetime_timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Profile, UserSession
from apps.chat.models import Conversation, ConversationParticipant

RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")
PREFIX = "loadtest_"


class Command(BaseCommand):
    help = "Create temporary authenticated users and direct conversations for external k6 tests."

    def add_arguments(self, parser):
        parser.add_argument("--users", type=int, default=100)
        parser.add_argument("--run-id", default=f"run-{timezone.now():%Y%m%d%H%M%S}")
        parser.add_argument("--output", default="loadtests/data/users.json")
        parser.add_argument("--token-hours", type=int, default=2)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        if getattr(settings, "CENTRAL_AUTH_ENABLED", False):
            raise CommandError("This load-test fixture command supports standalone local authentication only.")
        if str(os.getenv("ALLOW_LOAD_TEST_DATA", "")).strip().lower() not in {"1", "true", "yes", "on"}:
            raise CommandError("Set ALLOW_LOAD_TEST_DATA=true only for a controlled load-test window.")
        if not options["confirm"]:
            raise CommandError("Pass --confirm to acknowledge that temporary production database rows will be created.")

        count = int(options["users"])
        if count < 2 or count > 2000:
            raise CommandError("--users must be between 2 and 2000.")
        if count % 2:
            count += 1
            self.stdout.write(self.style.WARNING(f"Rounded user count up to {count} so every user has a partner."))

        run_id = str(options["run_id"]).strip().lower()
        if not RUN_ID_RE.fullmatch(run_id):
            raise CommandError("--run-id must contain 3-32 lowercase letters, digits, underscores, or hyphens.")
        token_hours = int(options["token_hours"])
        if token_hours < 1 or token_hours > 4:
            raise CommandError("--token-hours must be between 1 and 4.")

        User = get_user_model()
        username_prefix = f"{PREFIX}{run_id}_"
        existing = User.objects.filter(username__startswith=username_prefix)
        if existing.exists() and not options["force"]:
            raise CommandError(f"Load-test data for {run_id!r} already exists. Use --force to replace it.")

        output = Path(options["output"]).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)

        with transaction.atomic():
            if options["force"]:
                Conversation.objects.filter(direct_key__startswith=f"loadtest:{run_id}:").delete()
                existing.delete()

            users = []
            for index in range(count):
                username = f"{username_prefix}{index:04d}"
                user = User(
                    username=username,
                    email=f"{username}@loadtest.invalid",
                    email_verified=True,
                    email_verified_at=timezone.now(),
                    is_active=True,
                )
                user.set_unusable_password()
                users.append(user)
            User.objects.bulk_create(users, batch_size=250)
            users = list(User.objects.filter(username__startswith=username_prefix).order_by("username"))
            Profile.objects.bulk_create(
                [Profile(user=user, display_name=f"Load Test {index + 1}") for index, user in enumerate(users)],
                batch_size=250,
                ignore_conflicts=True,
            )

            conversations = []
            for pair_index in range(0, count, 2):
                first = users[pair_index]
                conversations.append(
                    Conversation(
                        type=Conversation.ConversationType.DIRECT,
                        title=f"Load test {run_id} pair {pair_index // 2 + 1}",
                        created_by=first,
                        direct_key=f"loadtest:{run_id}:{pair_index // 2}",
                        is_active=True,
                    )
                )
            Conversation.objects.bulk_create(conversations, batch_size=250)
            conversations = list(
                Conversation.objects.filter(direct_key__startswith=f"loadtest:{run_id}:").order_by("direct_key")
            )

            participants = []
            for pair_index, conversation in enumerate(conversations):
                first = users[pair_index * 2]
                second = users[pair_index * 2 + 1]
                participants.extend(
                    [
                        ConversationParticipant(
                            conversation=conversation,
                            user=first,
                            role=ConversationParticipant.Role.OWNER,
                        ),
                        ConversationParticipant(
                            conversation=conversation,
                            user=second,
                            role=ConversationParticipant.Role.MEMBER,
                        ),
                    ]
                )
            ConversationParticipant.objects.bulk_create(participants, batch_size=500)

            expires_at = timezone.now() + timedelta(hours=token_hours)
            records = []
            sessions = []
            for index, user in enumerate(users):
                pair_index = index // 2
                partner = users[index + 1] if index % 2 == 0 else users[index - 1]
                conversation = conversations[pair_index]
                session_id = uuid.uuid4()
                refresh = RefreshToken.for_user(user)
                refresh["session_id"] = str(session_id)
                access = refresh.access_token
                access["session_id"] = str(session_id)
                access.set_exp(from_time=timezone.now(), lifetime=timedelta(hours=token_hours))
                sessions.append(
                    UserSession(
                        id=session_id,
                        user=user,
                        refresh_jti=str(refresh.payload.get("jti", ""))[:64],
                        device_id=f"loadtest-{run_id}-{index:04d}",
                        user_agent="k6-realtime-load-test",
                        expires_at=expires_at,
                    )
                )
                records.append(
                    {
                        "user_id": str(user.id),
                        "username": user.username,
                        "access_token": str(access),
                        "device_id": f"loadtest-{run_id}-{index:04d}",
                        "device_type": "loadtest",
                        "conversation_id": str(conversation.id),
                        "partner_user_id": str(partner.id),
                    }
                )
            UserSession.objects.bulk_create(sessions, batch_size=250)

        document = {
            "schema_version": 1,
            "run_id": run_id,
            "generated_at": timezone.now().isoformat(),
            "expires_at": expires_at.astimezone(datetime_timezone.utc).isoformat(),
            "user_count": len(records),
            "users": records,
        }
        output.write_text(json.dumps(document, indent=2), encoding="utf-8")
        try:
            output.chmod(0o600)
        except OSError:
            pass
        self.stdout.write(self.style.SUCCESS(f"Created {len(records)} users and {len(conversations)} direct conversations."))
        self.stdout.write(f"Credential file: {output}")
        self.stdout.write(self.style.WARNING("Copy it securely to the external load generator, then delete both copies."))
