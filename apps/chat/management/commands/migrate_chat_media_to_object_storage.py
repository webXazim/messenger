from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.management.base import BaseCommand, CommandError

from apps.chat.models import MessageAttachment, PendingUpload
from apps.chat.storage import ChatPrivateS3Storage


class Command(BaseCommand):
    help = "Copy existing local chat files/thumbnails to the configured private S3/R2 storage without changing database names."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-root",
            default=str(settings.PRIVATE_MEDIA_ROOT),
            help="Local private-media directory to copy from.",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report work without uploading objects.")
        parser.add_argument(
            "--fail-on-missing",
            action="store_true",
            help="Exit non-zero when a database-referenced local source file is missing.",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "CHAT_USE_S3_STORAGE", False):
            raise CommandError("Enable CHAT_USE_R2_STORAGE/CHAT_USE_S3_STORAGE before running this command.")
        if ChatPrivateS3Storage is None:
            raise CommandError("django-storages/boto3 is not installed.")

        source_root = Path(options["source_root"]).expanduser().resolve()
        if not source_root.exists():
            raise CommandError(f"Source directory does not exist: {source_root}")

        source = FileSystemStorage(location=str(source_root))
        target = ChatPrivateS3Storage()
        dry_run = bool(options["dry_run"])
        copied = skipped = missing = 0
        seen = set()

        querysets = (
            MessageAttachment.objects.only("file", "thumbnail").iterator(chunk_size=500),
            PendingUpload.objects.only("file", "thumbnail").iterator(chunk_size=500),
        )

        for queryset in querysets:
            for record in queryset:
                for field_name in ("file", "thumbnail"):
                    field = getattr(record, field_name, None)
                    name = str(getattr(field, "name", "") or "")
                    if not name or name in seen:
                        continue
                    seen.add(name)

                    if target.exists(name):
                        skipped += 1
                        continue
                    if not source.exists(name):
                        missing += 1
                        self.stderr.write(self.style.WARNING(f"Missing local source: {name}"))
                        continue
                    if dry_run:
                        self.stdout.write(f"Would copy: {name}")
                        copied += 1
                        continue

                    with source.open(name, "rb") as source_file:
                        saved_name = target.save(name, source_file)
                    if saved_name != name:
                        raise CommandError(
                            f"Object storage changed key {name!r} to {saved_name!r}; aborting to preserve database references."
                        )
                    copied += 1
                    self.stdout.write(f"Copied: {name}")

        summary = f"Copied {copied}, already present {skipped}, missing local source {missing}."
        if missing and options["fail_on_missing"]:
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))
