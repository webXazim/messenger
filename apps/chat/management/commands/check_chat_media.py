from django.core.management.base import BaseCommand, CommandError

from apps.chat.models import MessageAttachment, PendingUpload


class Command(BaseCommand):
    help = "Check that database media records still have their stored files and thumbnails."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-on-missing",
            action="store_true",
            help="Exit with a non-zero status when one or more stored files are missing.",
        )
        parser.add_argument(
            "--show",
            type=int,
            default=25,
            help="Maximum number of missing records to print per model (default: 25).",
        )

    @staticmethod
    def _field_exists(field):
        if not field or not getattr(field, "name", ""):
            return False
        try:
            return field.storage.exists(field.name)
        except Exception:
            return False

    def _inspect_queryset(self, label, queryset, show_limit):
        total = 0
        missing_files = []
        missing_thumbnails = []

        for item in queryset.iterator(chunk_size=500):
            total += 1
            if not self._field_exists(item.file):
                missing_files.append((str(item.id), item.original_name, getattr(item.file, "name", "")))
            thumbnail = getattr(item, "thumbnail", None)
            if thumbnail and getattr(thumbnail, "name", "") and not self._field_exists(thumbnail):
                missing_thumbnails.append((str(item.id), item.original_name, thumbnail.name))

        self.stdout.write(
            f"- {label}: {total} records, {len(missing_files)} missing files, "
            f"{len(missing_thumbnails)} missing thumbnails"
        )

        for record_id, original_name, storage_name in missing_files[:show_limit]:
            self.stdout.write(
                self.style.WARNING(
                    f"  missing file: id={record_id} name={original_name!r} storage={storage_name!r}"
                )
            )
        if len(missing_files) > show_limit:
            self.stdout.write(f"  ... {len(missing_files) - show_limit} more missing files")

        for record_id, original_name, storage_name in missing_thumbnails[:show_limit]:
            self.stdout.write(
                self.style.WARNING(
                    f"  missing thumbnail: id={record_id} name={original_name!r} storage={storage_name!r}"
                )
            )
        if len(missing_thumbnails) > show_limit:
            self.stdout.write(f"  ... {len(missing_thumbnails) - show_limit} more missing thumbnails")

        return len(missing_files) + len(missing_thumbnails)

    def handle(self, *args, **options):
        show_limit = max(0, int(options["show"]))
        self.stdout.write(self.style.NOTICE("Messenger media storage integrity"))

        missing = 0
        missing += self._inspect_queryset(
            "message attachments",
            MessageAttachment.objects.only("id", "original_name", "file", "thumbnail"),
            show_limit,
        )
        missing += self._inspect_queryset(
            "pending uploads",
            PendingUpload.objects.only("id", "original_name", "file", "thumbnail"),
            show_limit,
        )

        if missing:
            message = (
                f"Detected {missing} missing stored media object(s). Database rows cannot recreate deleted "
                "files; restore the private_media Docker volume or storage backup."
            )
            if options["fail_on_missing"]:
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(message))
        else:
            self.stdout.write(self.style.SUCCESS("All referenced media objects are present."))
