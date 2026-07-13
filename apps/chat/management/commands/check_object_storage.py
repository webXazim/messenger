from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError

from apps.chat.storage import attachment_storage_factory


class Command(BaseCommand):
    help = "Write, read, and delete a temporary object using the configured chat storage backend."

    def handle(self, *args, **options):
        storage = attachment_storage_factory()
        key = f"healthchecks/{uuid4().hex}.txt"
        payload = b"crescentsphere-messenger-storage-ok\n"
        saved_name = ""

        self.stdout.write(f"Storage backend: {storage.__class__.__module__}.{storage.__class__.__name__}")
        try:
            saved_name = storage.save(key, ContentFile(payload))
            if not storage.exists(saved_name):
                raise CommandError("The storage backend accepted the upload but the object does not exist.")
            with storage.open(saved_name, "rb") as stored_file:
                downloaded = stored_file.read()
            if downloaded != payload:
                raise CommandError("The storage backend returned different content from the uploaded test object.")
        except Exception as exc:
            if isinstance(exc, CommandError):
                raise
            raise CommandError(f"Object storage check failed: {exc}") from exc
        finally:
            if saved_name:
                try:
                    storage.delete(saved_name)
                except Exception as exc:
                    self.stderr.write(self.style.WARNING(f"Could not delete test object {saved_name!r}: {exc}"))

        self.stdout.write(self.style.SUCCESS("Chat object storage is writable, readable, and deletable."))
