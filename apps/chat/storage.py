from django.conf import settings
from django.core.files.storage import FileSystemStorage

try:
    from storages.backends.s3boto3 import S3Boto3Storage
except Exception:  # pragma: no cover
    S3Boto3Storage = None


class PrivateFileSystemStorage(FileSystemStorage):
    def url(self, name):  # pragma: no cover
        return super().url(name)


if S3Boto3Storage:
    class ChatPrivateS3Storage(S3Boto3Storage):
        """Private chat-object storage for AWS S3 or Cloudflare R2.

        Browser access is always temporary/presigned. R2 custom domains are not used
        because chat attachments are private and presigned S3 URLs are required.
        """

        location = "chat-private"
        default_acl = "private"
        file_overwrite = False
        querystring_auth = True
        custom_domain = False
        bucket_name = settings.AWS_STORAGE_BUCKET_NAME
        access_key = settings.AWS_ACCESS_KEY_ID
        secret_key = settings.AWS_SECRET_ACCESS_KEY
        endpoint_url = settings.AWS_S3_ENDPOINT_URL or None
        region_name = settings.AWS_S3_REGION_NAME or None
        signature_version = settings.AWS_S3_SIGNATURE_VERSION
        addressing_style = settings.AWS_S3_ADDRESSING_STYLE
        querystring_expire = settings.AWS_QUERYSTRING_EXPIRE
        verify = settings.AWS_S3_VERIFY
else:  # pragma: no cover
    ChatPrivateS3Storage = None


def _private_storage():
    if getattr(settings, "CHAT_USE_S3_STORAGE", False):
        if ChatPrivateS3Storage is None:
            raise RuntimeError(
                "CHAT_USE_S3_STORAGE is enabled but django-storages/boto3 is not installed."
            )
        return ChatPrivateS3Storage()
    return PrivateFileSystemStorage(
        location=str(settings.PRIVATE_MEDIA_ROOT),
        base_url=settings.PRIVATE_MEDIA_URL,
    )


def pending_upload_storage_factory():
    return _private_storage()


def attachment_storage_factory():
    return _private_storage()
