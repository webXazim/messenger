from __future__ import annotations

import hashlib
import sys
import tempfile
from datetime import timedelta
from pathlib import PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Upload an encrypted system backup from stdin to a private Cloudflare R2 backup prefix."

    def add_arguments(self, parser):
        parser.add_argument("--object-name", required=True)
        parser.add_argument("--sha256", default="")
        parser.add_argument("--hmac-sha256", default="")
        parser.add_argument("--content-type", default="application/octet-stream")
        parser.add_argument("--retention-days", type=int, default=None)
        parser.add_argument("--keep-latest", type=int, default=None)

    def handle(self, *args, **options):
        bucket = str(getattr(settings, "BACKUP_R2_BUCKET_NAME", "") or "").strip()
        if not bucket:
            raise CommandError("BACKUP_R2_BUCKET_NAME is required")
        prefix = str(getattr(settings, "BACKUP_R2_PREFIX", "system-backups") or "system-backups").strip("/")
        object_name = PurePosixPath(str(options["object_name"]).lstrip("/"))
        if ".." in object_name.parts or object_name.is_absolute():
            raise CommandError("Unsafe object name")
        key = str(PurePosixPath(prefix) / object_name)

        client = self._client()
        max_bytes = max(1, int(getattr(settings, "BACKUP_MAX_UPLOAD_BYTES", 2_147_483_648)))
        digest = hashlib.sha256()
        total = 0
        with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as payload:
            while True:
                chunk = sys.stdin.buffer.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise CommandError(f"Backup exceeds BACKUP_MAX_UPLOAD_BYTES ({max_bytes})")
                digest.update(chunk)
                payload.write(chunk)
            if total == 0:
                raise CommandError("Backup input is empty")
            actual_sha = digest.hexdigest()
            expected_sha = str(options["sha256"] or "").strip().lower()
            if expected_sha and actual_sha != expected_sha:
                raise CommandError("Backup SHA-256 mismatch")
            payload.seek(0)
            metadata = {"sha256": actual_sha, "encrypted": "true"}
            hmac_sha = str(options["hmac_sha256"] or "").strip().lower()
            if hmac_sha:
                if len(hmac_sha) != 64 or any(char not in "0123456789abcdef" for char in hmac_sha):
                    raise CommandError("Invalid HMAC-SHA256 value")
                metadata["hmac-sha256"] = hmac_sha
            client.upload_fileobj(
                payload,
                bucket,
                key,
                ExtraArgs={"ContentType": options["content_type"], "Metadata": metadata},
            )

        head = client.head_object(Bucket=bucket, Key=key)
        if int(head.get("ContentLength") or 0) != total:
            raise CommandError("Uploaded backup size verification failed")
        remote_metadata = head.get("Metadata") or {}
        remote_sha = remote_metadata.get("sha256", "")
        if remote_sha != actual_sha:
            raise CommandError("Uploaded backup checksum metadata verification failed")
        expected_hmac = str(options["hmac_sha256"] or "").strip().lower()
        if expected_hmac and remote_metadata.get("hmac-sha256", "") != expected_hmac:
            raise CommandError("Uploaded backup HMAC metadata verification failed")

        retention_days = options["retention_days"]
        if retention_days is None:
            retention_days = int(getattr(settings, "BACKUP_R2_RETENTION_DAYS", 30))
        keep_latest = options["keep_latest"]
        if keep_latest is None:
            keep_latest = int(getattr(settings, "BACKUP_R2_KEEP_LATEST", 7))
        deleted = self._prune(client, bucket, prefix, max(1, retention_days), max(1, keep_latest))
        self.stdout.write(
            self.style.SUCCESS(
                f"Uploaded s3://{bucket}/{key} bytes={total} sha256={actual_sha} pruned={deleted}"
            )
        )

    @staticmethod
    def _client():
        try:
            import boto3
        except ImportError as exc:
            raise CommandError("boto3 is required") from exc
        endpoint = str(getattr(settings, "CLOUDFLARE_R2_ENDPOINT_URL", "") or "").strip()
        account_id = str(getattr(settings, "CLOUDFLARE_R2_ACCOUNT_ID", "") or "").strip()
        if not endpoint and account_id:
            endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        access_key = str(getattr(settings, "CLOUDFLARE_R2_ACCESS_KEY_ID", "") or "").strip()
        secret_key = str(getattr(settings, "CLOUDFLARE_R2_SECRET_ACCESS_KEY", "") or "").strip()
        if not endpoint or not access_key or not secret_key:
            raise CommandError("Cloudflare R2 endpoint and credentials are required")
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )

    @staticmethod
    def _prune(client, bucket: str, prefix: str, retention_days: int, keep_latest: int) -> int:
        cutoff = timezone.now() - timedelta(days=retention_days)
        paginator = client.get_paginator("list_objects_v2")
        objects = []
        for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/"):
            objects.extend(page.get("Contents") or [])
        objects.sort(key=lambda item: item.get("LastModified"), reverse=True)
        candidates = [
            item for index, item in enumerate(objects)
            if index >= keep_latest and item.get("LastModified") and item["LastModified"] < cutoff
        ]
        deleted = 0
        for offset in range(0, len(candidates), 1000):
            batch = candidates[offset:offset + 1000]
            response = client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": item["Key"]} for item in batch], "Quiet": False},
            )
            if response.get("Errors"):
                raise CommandError(f"R2 retention deletion failed: {response['Errors'][:3]}")
            deleted += len(response.get("Deleted") or [])
        return deleted
