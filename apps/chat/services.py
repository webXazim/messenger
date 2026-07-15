import hashlib
import json
import logging
import re
import shutil
import subprocess
import time
import wave
import sys
from array import array
from contextlib import contextmanager
from uuid import uuid4
import mimetypes
import uuid
from io import BytesIO
from pathlib import Path
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from tempfile import NamedTemporaryFile

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.base import File
from django.db import IntegrityError, connection, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import PermissionDenied, ValidationError
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

from .antivirus import scan_file_field
from .models import (
    CallParticipant,
    CallSession,
    ChatAuditLog,
    Conversation,
    ConversationInviteLink,
    ConversationNotificationSetting,
    ConversationParticipant,
    Message,
    MessageAttachment,
    MessageAttachmentViewReceipt,
    MessageDelivery,
    MessageEditHistory,
    MessageReaction,
    MessageReport,
    MessageTranscript,
    ModerationAction,
    NotificationPreference,
    PendingUpload,
    UserE2EEDeviceKey,
    UserBlock,
    UserDevice,
)

logger = logging.getLogger(__name__)
User = get_user_model()
MEDIA_TOKEN_SALT = "chat-media-access"
GROUP_ROUTE_NAME_MAX_LENGTH = 80


def normalize_group_route_name(value):
    return slugify(str(value or ""), allow_unicode=True).replace("_", "-")[:GROUP_ROUTE_NAME_MAX_LENGTH]


def group_route_name_is_available(value):
    normalized = normalize_group_route_name(value)
    if len(normalized) < 3:
        return False, normalized, "Use at least three letters or numbers."
    unavailable = Conversation.objects.filter(slug__iexact=normalized).exists() or User.objects.filter(username__iexact=normalized).exists()
    return (not unavailable), normalized, "This unique name is already in use." if unavailable else ""
CALL_SIGNAL_QUEUE_TTL_SECONDS = int(getattr(settings, "CALL_SIGNAL_QUEUE_TTL_SECONDS", 180) or 180)
CALL_SIGNAL_DEDUP_TTL_SECONDS = int(getattr(settings, "CALL_SIGNAL_DEDUP_TTL_SECONDS", max(CALL_SIGNAL_QUEUE_TTL_SECONDS * 2, 300)) or max(CALL_SIGNAL_QUEUE_TTL_SECONDS * 2, 300))
ACTIVE_CALL_STATUSES = (CallSession.Status.INITIATED, CallSession.Status.RINGING, CallSession.Status.ONGOING)
AUTO_HIDE_REPORT_THRESHOLD = int(getattr(settings, "AUTO_HIDE_REPORT_THRESHOLD", 3) or 3)
MESSAGE_DUPLICATE_WINDOW_SECONDS = int(getattr(settings, "MESSAGE_DUPLICATE_WINDOW_SECONDS", 120) or 120)
MESSAGE_DUPLICATE_THRESHOLD = int(getattr(settings, "MESSAGE_DUPLICATE_THRESHOLD", 3) or 3)
MESSAGE_EDIT_WINDOW_SECONDS = max(0, int(getattr(settings, "MESSAGE_EDIT_WINDOW_SECONDS", 15 * 60) or 0))
MESSAGE_BURST_WINDOW_SECONDS = int(getattr(settings, "MESSAGE_BURST_WINDOW_SECONDS", 30) or 30)
MESSAGE_BURST_THRESHOLD = int(getattr(settings, "MESSAGE_BURST_THRESHOLD", 20) or 20)
MESSAGE_MAX_LINKS = int(getattr(settings, "MESSAGE_MAX_LINKS", 5) or 5)
MESSAGE_MAX_CIPHERTEXT_BYTES = int(getattr(settings, "MESSAGE_MAX_CIPHERTEXT_BYTES", 256 * 1024) or 256 * 1024)
MESSAGE_MAX_ENCRYPTION_ENVELOPE_BYTES = int(getattr(settings, "MESSAGE_MAX_ENCRYPTION_ENVELOPE_BYTES", 300 * 1024) or 300 * 1024)
MEDIA_METADATA_MAX_BYTES = int(getattr(settings, "MEDIA_METADATA_MAX_BYTES", 16 * 1024) or 16 * 1024)
MEDIA_THUMBNAIL_MAX_BYTES = int(getattr(settings, "MEDIA_THUMBNAIL_MAX_BYTES", 4 * 1024 * 1024) or 4 * 1024 * 1024)
MEDIA_THUMBNAIL_MAX_DIMENSION = int(getattr(settings, "MEDIA_THUMBNAIL_MAX_DIMENSION", 2048) or 2048)
MEDIA_SERVER_THUMBNAIL_DIMENSION = int(getattr(settings, "MEDIA_SERVER_THUMBNAIL_DIMENSION", 160) or 160)
MEDIA_SERVER_THUMBNAIL_JPEG_QUALITY = int(getattr(settings, "MEDIA_SERVER_THUMBNAIL_JPEG_QUALITY", 18) or 18)
MEDIA_PROBE_TIMEOUT_SECONDS = float(getattr(settings, "MEDIA_PROBE_TIMEOUT_SECONDS", 4.0) or 4.0)
MEDIA_THUMBNAIL_GENERATION_TIMEOUT_SECONDS = float(getattr(settings, "MEDIA_THUMBNAIL_GENERATION_TIMEOUT_SECONDS", 6.0) or 6.0)
MEDIA_VIDEO_THUMBNAIL_OFFSET_SECONDS = float(getattr(settings, "MEDIA_VIDEO_THUMBNAIL_OFFSET_SECONDS", 0.25) or 0.25)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
MEDIA_METADATA_RESERVED_KEYS = {"encrypted_attachment", "encryption"}


class CallParticipantBusy(Exception):
    def __init__(self, busy_user_ids, *, active_call_id=None, actor_busy=False):
        self.busy_user_ids = [str(user_id) for user_id in busy_user_ids]
        self.active_call_id = str(active_call_id or "")
        self.actor_busy = bool(actor_busy)
        super().__init__("One or more participants are already in another call.")


def _call_signal_cache_key(call_id, user_id):
    return f"call:{call_id}:signals:{user_id}"


def _call_signal_dedupe_cache_key(call_id, user_id, signal_id):
    return f"call:{call_id}:signal-dedupe:{user_id}:{signal_id}"


def _normalize_signal_id(value):
    value = str(value or "").strip()
    return value[:128] if value else ""


def _append_pending_call_signal(call_id, user_id, signal):
    if not user_id:
        return False
    signal = dict(signal or {})
    signal_id = _normalize_signal_id(signal.get("signal_id"))
    if not signal_id:
        signal_id = uuid4().hex
        signal["signal_id"] = signal_id
    dedupe_key = _call_signal_dedupe_cache_key(call_id, user_id, signal_id)
    if cache.get(dedupe_key):
        return False
    cache.set(dedupe_key, True, timeout=CALL_SIGNAL_DEDUP_TTL_SECONDS)
    key = _call_signal_cache_key(call_id, user_id)
    pending = cache.get(key) or []
    pending.append(signal)
    cache.set(key, pending[-100:], timeout=CALL_SIGNAL_QUEUE_TTL_SECONDS)
    return True


def _pop_pending_call_signals(call_id, user_id):
    if not user_id:
        return []
    key = _call_signal_cache_key(call_id, user_id)
    pending = cache.get(key) or []
    cache.delete(key)
    unique = []
    seen = set()
    for item in pending:
        signal_id = _normalize_signal_id((item or {}).get("signal_id"))
        if signal_id:
            if signal_id in seen:
                continue
            seen.add(signal_id)
        unique.append(item)
    return unique


def is_voice_like_upload(upload) -> bool:
    mime = (upload.mime_type or "").lower().strip()
    ext = (upload.extension or "").lower().strip().lstrip(".")
    return mime.startswith("audio/") or mime in VOICE_MIME_PREFIXES or ext in VOICE_EXTENSIONS


def media_kind_from_mime(mime_type: str) -> str:
    mime = (mime_type or "").lower().strip()
    if mime.startswith("image/"):
        return PendingUpload.MediaKind.IMAGE
    if mime.startswith("video/"):
        return PendingUpload.MediaKind.VIDEO
    if mime.startswith("audio/"):
        return PendingUpload.MediaKind.AUDIO
    return PendingUpload.MediaKind.FILE



def _clamp_int(value, *, minimum=0, maximum=100000):
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return None


def _clamp_float(value, *, minimum=0.0, maximum=100.0):
    try:
        return max(minimum, min(float(value), maximum))
    except (TypeError, ValueError):
        return None


def _compute_quality_score(*, packet_loss_pct=None, jitter_ms=None, round_trip_time_ms=None, bitrate_kbps=None, frame_rate=None, network_quality=None):
    score = 100.0
    if packet_loss_pct is not None:
        score -= min(float(packet_loss_pct) * 2.5, 45.0)
    if jitter_ms is not None:
        score -= min(max(float(jitter_ms) - 20.0, 0.0) * 0.4, 20.0)
    if round_trip_time_ms is not None:
        score -= min(max(float(round_trip_time_ms) - 120.0, 0.0) * 0.06, 18.0)
    if bitrate_kbps is not None and bitrate_kbps > 0:
        score -= 15.0 if bitrate_kbps < 120 else (8.0 if bitrate_kbps < 300 else 0.0)
    if frame_rate is not None and frame_rate > 0 and frame_rate < 12:
        score -= 8.0
    quality_penalty = {
        CallParticipant.NetworkQuality.EXCELLENT: 0.0,
        CallParticipant.NetworkQuality.GOOD: 3.0,
        CallParticipant.NetworkQuality.FAIR: 10.0,
        CallParticipant.NetworkQuality.POOR: 22.0,
        CallParticipant.NetworkQuality.OFFLINE: 40.0,
        CallParticipant.NetworkQuality.UNKNOWN: 4.0,
    }
    if network_quality:
        score -= quality_penalty.get(network_quality, 0.0)
    return max(0, min(int(round(score)), 100))


def _quality_alert_from_score(score):
    if score is None:
        return ""
    if score < 25:
        return "critical"
    if score < 45:
        return "poor"
    if score < 70:
        return "degraded"
    return ""




def _extract_links_from_text(text):
    import re
    if not text:
        return []
    pattern = re.compile(r"https?://[^\s<>()]+")
    seen = []
    for match in pattern.findall(text):
        if match not in seen:
            seen.append(match)
    return seen[:10]


def sanitize_chat_text(value, *, max_length=None, multiline=False):
    text = CONTROL_CHAR_RE.sub("", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if multiline:
        text = "\n".join(" ".join(line.split()) for line in text.split("\n")).strip()
    else:
        text = " ".join(text.split())
    if max_length is not None:
        text = text[:max_length]
    return text


def _prepare_message_metadata(*, conversation, metadata=None, entities=None, transcript_payload=None):
    metadata = dict(metadata or {})
    clean_entities = []
    mention_user_ids = []
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type") or "").strip().lower()
        if entity_type not in {"bold", "italic", "underline", "strike", "code", "link", "mention"}:
            continue
        item = {
            "type": entity_type,
            "offset": _clamp_int(entity.get("offset"), minimum=0, maximum=50000) or 0,
            "length": _clamp_int(entity.get("length"), minimum=1, maximum=50000) or 1,
        }
        if entity_type == "link":
            url = str(entity.get("url") or "").strip()
            if url:
                item["url"] = url[:1000]
        if entity_type == "mention":
            user_id = entity.get("user_id")
            if user_id:
                uid = str(user_id)
                item["user_id"] = uid
                mention_user_ids.append(uid)
                if entity.get("username"):
                    item["username"] = str(entity.get("username"))[:150]
        clean_entities.append(item)
    if mention_user_ids:
        valid_ids = set(
            ConversationParticipant.objects.filter(
                conversation=conversation,
                user_id__in=mention_user_ids,
                left_at__isnull=True,
                banned_at__isnull=True,
            ).values_list("user_id", flat=True)
        )
        clean_entities = [
            item for item in clean_entities
            if item.get("type") != "mention" or item.get("user_id") in {str(v) for v in valid_ids}
        ]
        metadata["mentioned_user_ids"] = [str(v) for v in valid_ids]
    elif metadata.get("mentioned_user_ids"):
        metadata.pop("mentioned_user_ids", None)
    if clean_entities:
        metadata["entities"] = clean_entities
    elif metadata.get("entities"):
        metadata.pop("entities", None)
    links = _extract_links_from_text(metadata.get("raw_text") or "")
    if links:
        metadata["links"] = links
    else:
        metadata.pop("links", None)
    if transcript_payload:
        metadata["transcript_requested"] = True
        if transcript_payload.get("language_code"):
            metadata["transcript_language_code"] = str(transcript_payload.get("language_code"))[:16]
    return metadata


def _json_payload_size(value):
    return len(json.dumps(value, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8"))


def _sanitize_media_metadata_value(value, *, depth=0):
    if depth > 4:
        raise ValidationError({"metadata": "Media metadata is nested too deeply."})
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        if len(value) > 128:
            raise ValidationError({"metadata": "Media metadata list is too large."})
        return [_sanitize_media_metadata_value(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if len(value) > 64:
            raise ValidationError({"metadata": "Media metadata object is too large."})
        cleaned = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()[:64]
            if not key:
                continue
            if key in MEDIA_METADATA_RESERVED_KEYS:
                raise ValidationError({"metadata": f"'{key}' is reserved and cannot be set by clients."})
            cleaned[key] = _sanitize_media_metadata_value(raw_value, depth=depth + 1)
        return cleaned
    raise ValidationError({"metadata": "Media metadata values must be JSON scalars, lists, or objects."})


def sanitize_media_metadata(metadata):
    if metadata in (None, "", {}):
        return {}
    if not isinstance(metadata, dict):
        raise ValidationError({"metadata": "Media metadata must be an object."})
    cleaned = _sanitize_media_metadata_value(metadata, depth=0)
    if _json_payload_size(cleaned) > MEDIA_METADATA_MAX_BYTES:
        raise ValidationError({"metadata": "Media metadata payload is too large."})
    return cleaned


def public_media_metadata(metadata):
    cleaned = {}
    for key, value in dict(metadata or {}).items():
        if key in MEDIA_METADATA_RESERVED_KEYS:
            continue
        cleaned[key] = value
    return cleaned


def _quantize_duration_seconds(value) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def _safe_delete_filefield(file_field):
    if not file_field:
        return
    try:
        file_field.delete(save=False)
    except Exception:
        logger.warning("Failed to delete media file field.", exc_info=True)


def _conversation_has_retained_participants(conversation):
    return conversation.participants.filter(
        left_at__isnull=True,
        is_archived=False,
    ).exists()


def _delete_conversation_storage(conversation):
    _safe_delete_filefield(conversation.avatar)
    attachments = MessageAttachment.objects.filter(message__conversation=conversation).only("id", "file", "thumbnail")
    for attachment in attachments.iterator(chunk_size=200):
        _safe_delete_filefield(attachment.thumbnail)
        _safe_delete_filefield(attachment.file)


def cleanup_conversation_if_unretained(conversation):
    if _conversation_has_retained_participants(conversation):
        return False
    _delete_conversation_storage(conversation)
    conversation.delete()
    return True


def delete_conversation(actor, conversation):
    participant = conversation.participants.filter(user=actor, left_at__isnull=True).first()
    if participant is None and not getattr(actor, "is_staff", False):
        raise ValidationError({"conversation": "You are not a participant in this conversation."})
    if conversation.type == Conversation.ConversationType.GROUP:
        is_owner = participant and participant.role == ConversationParticipant.Role.OWNER
        if not is_owner and not getattr(actor, "is_staff", False):
            raise ValidationError({"conversation": "Only the group owner can delete the full chat."})
    _delete_conversation_storage(conversation)
    conversation.delete()
    return True


def _replace_public_media_metadata(metadata, **updates):
    merged = public_media_metadata(metadata)
    for key, value in updates.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def _read_filefield_bytes(file_field):
    if not file_field:
        raise ValueError("A file is required.")

    file_name = getattr(file_field, "name", "") or ""
    if hasattr(file_field, "storage") and file_name:
        with file_field.storage.open(file_name, "rb") as source:
            return source.read()

    with file_field.open("rb") as source:
        return source.read()


def _iter_filefield_chunks(file_field, chunk_size=1024 * 1024):
    if not file_field:
        raise ValueError("A file is required.")

    file_name = getattr(file_field, "name", "") or ""
    if hasattr(file_field, "storage") and file_name:
        with file_field.storage.open(file_name, "rb") as source:
            while True:
                chunk = source.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        return

    with file_field.open("rb") as source:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _coerce_positive_int(value):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _safe_fraction_to_float(value):
    raw_value = str(value or "").strip()
    if not raw_value or raw_value in {"0/0", "N/A"}:
        return None
    if "/" in raw_value:
        numerator, denominator = raw_value.split("/", 1)
        try:
            numerator_value = float(numerator)
            denominator_value = float(denominator)
        except ValueError:
            return None
        if not denominator_value:
            return None
        return numerator_value / denominator_value
    try:
        return float(raw_value)
    except ValueError:
        return None


def _extract_rotation_from_stream(stream):
    tags = stream.get("tags") or {}
    rotation = tags.get("rotate")
    if rotation is not None:
        try:
            return int(round(float(rotation))) % 360
        except (TypeError, ValueError):
            pass

    for side_data in stream.get("side_data_list") or []:
        raw_rotation = side_data.get("rotation")
        if raw_rotation is None:
            continue
        try:
            return int(round(float(raw_rotation))) % 360
        except (TypeError, ValueError):
            continue
    return None


def _filefield_suffix(file_field, fallback=".bin"):
    suffix = Path(getattr(file_field, "name", "") or "").suffix
    return suffix[:16] if suffix else fallback


def _safe_filefield_path(file_field):
    try:
        return file_field.path
    except Exception:
        return None


@contextmanager
def _materialized_filefield_path(file_field):
    temp_path = None
    source_path = _safe_filefield_path(file_field)
    if source_path:
        yield source_path
        return

    with NamedTemporaryFile(delete=False, suffix=_filefield_suffix(file_field)) as temp_file:
        for chunk in _iter_filefield_chunks(file_field):
            temp_file.write(chunk)
        temp_path = temp_file.name

    try:
        yield temp_path
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                logger.warning("Failed to delete temporary media file.", exc_info=True)


def _run_ffprobe_for_filefield(file_field):
    ffprobe_binary = shutil.which("ffprobe")
    if not ffprobe_binary:
        return None, "ffprobe_unavailable"

    with _materialized_filefield_path(file_field) as probe_path:
        command = [
            ffprobe_binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            probe_path,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=MEDIA_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "ffprobe_timeout"

    if result.returncode != 0:
        logger.warning("ffprobe failed for %s: %s", getattr(file_field, "name", ""), (result.stderr or "").strip())
        return None, "ffprobe_failed"
    try:
        return json.loads(result.stdout or "{}"), None
    except json.JSONDecodeError:
        return None, "ffprobe_invalid_json"


def _extract_av_media_details(upload):
    probe_payload, probe_error = _run_ffprobe_for_filefield(upload.file)
    if probe_error:
        return None, {"server_probe_status": probe_error}

    streams = probe_payload.get("streams") or []
    format_payload = probe_payload.get("format") or {}
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)

    details = {
        "duration_seconds": _quantize_duration_seconds(
            format_payload.get("duration")
            or (video_stream or {}).get("duration")
            or (audio_stream or {}).get("duration")
        ),
        "metadata_updates": {
            "server_probe_status": "ffprobe_verified",
            "bit_rate": _coerce_positive_int(format_payload.get("bit_rate")),
        },
    }

    if video_stream:
        width = _coerce_positive_int(video_stream.get("width"))
        height = _coerce_positive_int(video_stream.get("height"))
        rotation = _extract_rotation_from_stream(video_stream) or 0
        display_width = width
        display_height = height
        if rotation in {90, 270} and width and height:
            display_width, display_height = height, width
        details.update({
            "width": width,
            "height": height,
            "rotation": rotation,
        })
        details["metadata_updates"].update({
            "display_width": display_width,
            "display_height": display_height,
            "aspect_ratio": round(display_width / display_height, 6) if display_width and display_height else None,
            "codec_name": video_stream.get("codec_name") or None,
            "frame_rate": round(_safe_fraction_to_float(video_stream.get("avg_frame_rate")) or 0, 3) or None,
            "has_audio_stream": bool(audio_stream),
        })
    elif audio_stream:
        details["metadata_updates"].update({
            "codec_name": audio_stream.get("codec_name") or None,
            "sample_rate": _coerce_positive_int(audio_stream.get("sample_rate")),
            "channels": _coerce_positive_int(audio_stream.get("channels")),
        })

    return details, None


def _extract_video_thumbnail_details(upload):
    ffmpeg_binary = shutil.which("ffmpeg")
    if not ffmpeg_binary:
        return None, {"thumbnail_generation_status": "ffmpeg_unavailable"}

    with _materialized_filefield_path(upload.file) as media_path:
        command = [
            ffmpeg_binary,
            "-v",
            "error",
            "-ss",
            str(MEDIA_VIDEO_THUMBNAIL_OFFSET_SECONDS),
            "-i",
            media_path,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=MEDIA_THUMBNAIL_GENERATION_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, {"thumbnail_generation_status": "ffmpeg_timeout"}

    if result.returncode != 0 or not result.stdout:
        logger.warning("ffmpeg thumbnail generation failed for %s: %s", getattr(upload.file, "name", ""), (result.stderr or b"").decode("utf-8", errors="replace").strip())
        return None, {"thumbnail_generation_status": "ffmpeg_failed"}

    try:
        with BytesIO(result.stdout) as buffer:
            image = Image.open(buffer)
            image.load()
            normalized = ImageOps.exif_transpose(image).copy()
    except (UnidentifiedImageError, OSError, ValueError):
        return None, {"thumbnail_generation_status": "ffmpeg_invalid_thumbnail"}

    thumbnail_file, thumbnail_content_type, thumbnail_width, thumbnail_height = _build_thumbnail_content(
        normalized,
        original_name=upload.original_name,
        max_dimension=MEDIA_SERVER_THUMBNAIL_DIMENSION,
    )
    return {
        "thumbnail_file": thumbnail_file,
        "thumbnail_content_type": thumbnail_content_type,
        "thumbnail_width": thumbnail_width,
        "thumbnail_height": thumbnail_height,
    }, None


def _normalized_image_from_field(file_field):
    image_bytes = _read_filefield_bytes(file_field)
    def _load_image(*, allow_truncated=False):
        previous_value = ImageFile.LOAD_TRUNCATED_IMAGES
        ImageFile.LOAD_TRUNCATED_IMAGES = allow_truncated
        try:
            with BytesIO(image_bytes) as buffer:
                image = Image.open(buffer)
                image.load()
                return ImageOps.exif_transpose(image).copy()
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = previous_value

    try:
        normalized = _load_image()
    except OSError:
        normalized = _load_image(allow_truncated=True)
    if normalized.mode not in {"RGB", "RGBA"}:
        normalized = normalized.convert("RGBA" if "A" in normalized.getbands() else "RGB")
    return normalized


def _build_thumbnail_content(image: Image.Image, *, original_name: str, max_dimension: int):
    rendered = image.copy()
    rendered.thumbnail((max_dimension, max_dimension))
    rendered_width, rendered_height = rendered.size
    has_alpha = "A" in rendered.getbands()
    output = BytesIO()
    if has_alpha:
        background = Image.new("RGB", rendered.size, (18, 18, 18))
        background.paste(rendered, mask=rendered.getchannel("A"))
        rendered = background
    elif rendered.mode != "RGB":
        rendered = rendered.convert("RGB")
    rendered.save(
        output,
        format="JPEG",
        quality=MEDIA_SERVER_THUMBNAIL_JPEG_QUALITY,
        optimize=True,
        progressive=True,
    )
    suffix = ".jpg"
    content_type = "image/jpeg"
    stem = Path(original_name or "thumbnail").stem[:80] or "thumbnail"
    return (
        ContentFile(output.getvalue(), name=f"{stem}-thumb{suffix}"),
        content_type,
        rendered_width,
        rendered_height,
    )


def _extract_image_media_details(upload):
    image = _normalized_image_from_field(upload.file)
    width, height = image.size
    thumbnail_file, thumbnail_content_type, thumbnail_width, thumbnail_height = _build_thumbnail_content(
        image,
        original_name=upload.original_name,
        max_dimension=MEDIA_SERVER_THUMBNAIL_DIMENSION,
    )
    return {
        "width": width,
        "height": height,
        "rotation": 0,
        "thumbnail_file": thumbnail_file,
        "thumbnail_content_type": thumbnail_content_type,
        "thumbnail_width": thumbnail_width,
        "thumbnail_height": thumbnail_height,
    }


def _extract_wav_duration_seconds(upload):
    with upload.file.open("rb") as source:
        with wave.open(source, "rb") as wav_file:
            frames = wav_file.getnframes()
            frame_rate = wav_file.getframerate()
    if not frame_rate:
        return None
    return _quantize_duration_seconds(frames / frame_rate)


def _extract_audio_waveform(upload, sample_count=48):
    """Return a compact normalized waveform without retaining decoded audio."""
    ffmpeg_binary = shutil.which("ffmpeg")
    if not ffmpeg_binary:
        return None, "ffmpeg_unavailable"
    with _materialized_filefield_path(upload.file) as media_path:
        command = [
            ffmpeg_binary,
            "-v", "error",
            "-i", media_path,
            "-map", "0:a:0",
            "-ac", "1",
            "-ar", "8000",
            "-f", "s16le",
            "-",
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=MEDIA_THUMBNAIL_GENERATION_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "ffmpeg_timeout"
    if result.returncode != 0 or len(result.stdout) < 2:
        return None, "ffmpeg_failed"
    samples = array("h")
    samples.frombytes(result.stdout[:len(result.stdout) - (len(result.stdout) % 2)])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return None, "empty_audio"
    bucket_size = max(1, len(samples) // sample_count)
    peaks = []
    for index in range(sample_count):
        start = index * bucket_size
        end = len(samples) if index == sample_count - 1 else min(len(samples), start + bucket_size)
        peaks.append(max((abs(value) for value in samples[start:end]), default=0))
    maximum = max(peaks) or 1
    return [max(7, min(100, round((peak / maximum) * 100))) for peak in peaks], "generated"


def _validate_or_normalize_thumbnail(upload):
    if not upload.thumbnail:
        return None
    thumbnail_content_type = (getattr(upload.thumbnail, "content_type", "") or "").lower()
    if thumbnail_content_type and not thumbnail_content_type.startswith("image/"):
        _safe_delete_filefield(upload.thumbnail)
        return {"removed_invalid_thumbnail": True, "thumbnail_rejected_reason": "invalid_content_type"}
    if getattr(upload.thumbnail, "size", 0) > MEDIA_THUMBNAIL_MAX_BYTES:
        _safe_delete_filefield(upload.thumbnail)
        return {"removed_invalid_thumbnail": True, "thumbnail_rejected_reason": "thumbnail_too_large"}
    try:
        image = _normalized_image_from_field(upload.thumbnail)
    except (UnidentifiedImageError, OSError, ValueError):
        _safe_delete_filefield(upload.thumbnail)
        return {"removed_invalid_thumbnail": True}
    width, height = image.size
    if width <= 0 or height <= 0:
        _safe_delete_filefield(upload.thumbnail)
        return {"removed_invalid_thumbnail": True}
    thumbnail_file, content_type, normalized_width, normalized_height = _build_thumbnail_content(
        image,
        original_name=upload.original_name or upload.thumbnail.name,
        max_dimension=MEDIA_SERVER_THUMBNAIL_DIMENSION,
    )
    _safe_delete_filefield(upload.thumbnail)
    upload.thumbnail.save(Path(thumbnail_file.name).name, thumbnail_file, save=False)
    return {
        "thumbnail_content_type": content_type,
        "thumbnail_width": normalized_width,
        "thumbnail_height": normalized_height,
    }


def enrich_pending_upload_media(upload):
    updates = []
    metadata = dict(upload.metadata or {})

    thumbnail_result = _validate_or_normalize_thumbnail(upload)
    if thumbnail_result is not None:
        metadata = _replace_public_media_metadata(metadata, **thumbnail_result)
        updates.extend(["thumbnail", "metadata"])

    try:
        if upload.media_kind == PendingUpload.MediaKind.IMAGE:
            details = _extract_image_media_details(upload)
            if upload.width != details["width"]:
                upload.width = details["width"]
                updates.append("width")
            if upload.height != details["height"]:
                upload.height = details["height"]
                updates.append("height")
            if upload.rotation != details["rotation"]:
                upload.rotation = details["rotation"]
                updates.append("rotation")
            if not upload.thumbnail:
                upload.thumbnail.save(Path(details["thumbnail_file"].name).name, details["thumbnail_file"], save=False)
                updates.append("thumbnail")
                metadata = _replace_public_media_metadata(
                    metadata,
                    thumbnail_source="server_generated",
                    thumbnail_content_type=details["thumbnail_content_type"],
                    thumbnail_width=details["thumbnail_width"],
                    thumbnail_height=details["thumbnail_height"],
                )
            metadata = _replace_public_media_metadata(
                metadata,
                display_width=upload.width,
                display_height=upload.height,
                aspect_ratio=round(upload.width / upload.height, 6) if upload.width and upload.height else None,
                server_metadata_verified=True,
                server_metadata_verified_at=timezone.now().isoformat(),
            )
            updates.append("metadata")
        elif upload.media_kind == PendingUpload.MediaKind.VIDEO:
            details, probe_metadata = _extract_av_media_details(upload)
            if details:
                if details.get("width") and upload.width != details["width"]:
                    upload.width = details["width"]
                    updates.append("width")
                if details.get("height") and upload.height != details["height"]:
                    upload.height = details["height"]
                    updates.append("height")
                if details.get("rotation") is not None and upload.rotation != details["rotation"]:
                    upload.rotation = details["rotation"]
                    updates.append("rotation")
                if details.get("duration_seconds") is not None and upload.duration_seconds != details["duration_seconds"]:
                    upload.duration_seconds = details["duration_seconds"]
                    updates.append("duration_seconds")
                metadata = _replace_public_media_metadata(
                    metadata,
                    **details.get("metadata_updates", {}),
                    server_metadata_verified=True,
                    server_metadata_verified_at=timezone.now().isoformat(),
                )
                if not upload.thumbnail:
                    thumbnail_details, thumbnail_metadata = _extract_video_thumbnail_details(upload)
                    if thumbnail_details:
                        upload.thumbnail.save(Path(thumbnail_details["thumbnail_file"].name).name, thumbnail_details["thumbnail_file"], save=False)
                        updates.append("thumbnail")
                        metadata = _replace_public_media_metadata(
                            metadata,
                            thumbnail_source="server_generated",
                            thumbnail_content_type=thumbnail_details["thumbnail_content_type"],
                            thumbnail_width=thumbnail_details["thumbnail_width"],
                            thumbnail_height=thumbnail_details["thumbnail_height"],
                            thumbnail_generation_status="generated",
                        )
                    else:
                        metadata = _replace_public_media_metadata(
                            metadata,
                            **(thumbnail_metadata or {}),
                        )
            else:
                metadata = _replace_public_media_metadata(
                    metadata,
                    **(probe_metadata or {}),
                )
            updates.append("metadata")
        elif upload.media_kind == PendingUpload.MediaKind.AUDIO:
            details, probe_metadata = _extract_av_media_details(upload)
            if details:
                if details.get("duration_seconds") is not None and upload.duration_seconds != details["duration_seconds"]:
                    upload.duration_seconds = details["duration_seconds"]
                    updates.append("duration_seconds")
                metadata = _replace_public_media_metadata(
                    metadata,
                    **details.get("metadata_updates", {}),
                    server_metadata_verified=True,
                    server_metadata_verified_at=timezone.now().isoformat(),
                )
            elif (upload.mime_type or "").lower() in {"audio/wav", "audio/x-wav", "audio/wave"} and not upload.duration_seconds:
                duration_seconds = _extract_wav_duration_seconds(upload)
                if duration_seconds is not None:
                    upload.duration_seconds = duration_seconds
                    updates.append("duration_seconds")
                metadata = _replace_public_media_metadata(
                    metadata,
                    server_probe_status=(probe_metadata or {}).get("server_probe_status"),
                    server_metadata_verified=True,
                    server_metadata_verified_at=timezone.now().isoformat(),
                )
            else:
                metadata = _replace_public_media_metadata(
                    metadata,
                    **(probe_metadata or {}),
                )
            metadata = _replace_public_media_metadata(
                metadata,
                server_metadata_verified=True if upload.duration_seconds is not None or details else metadata.get("server_metadata_verified"),
                server_metadata_verified_at=timezone.now().isoformat() if upload.duration_seconds is not None or details else metadata.get("server_metadata_verified_at"),
            )
            waveform, waveform_status = _extract_audio_waveform(upload)
            metadata = _replace_public_media_metadata(
                metadata,
                waveform=waveform or metadata.get("waveform"),
                waveform_generation_status=waveform_status,
            )
            updates.append("metadata")
    except Exception as exc:
        logger.warning("Media enrichment failed for pending upload %s: %s", upload.id, exc, exc_info=True)
        metadata = _replace_public_media_metadata(
            metadata,
            media_enrichment_failed=True,
            media_enrichment_failed_at=timezone.now().isoformat(),
        )
        updates.append("metadata")

    upload.metadata = metadata
    if updates:
        normalized_updates = []
        for field in updates:
            if field not in normalized_updates:
                normalized_updates.append(field)
        upload.save(update_fields=normalized_updates + ["updated_at"])
    return upload


def _canonicalize_public_key_jwk(public_key_jwk):
    if not isinstance(public_key_jwk, dict) or not public_key_jwk:
        raise ValidationError({"public_key_jwk": "A public JWK is required."})
    return json.dumps(public_key_jwk, separators=(",", ":"), sort_keys=True)


def _fingerprint_public_key_jwk(public_key_jwk):
    canonical = _canonicalize_public_key_jwk(public_key_jwk)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _mark_conversation_e2ee_rekey_required(conversation, *, bump_version=True):
    now = timezone.now()
    updates = {
        "e2ee_rekey_required": True,
        "e2ee_last_security_event_at": now,
        "updated_at": now,
    }
    if bump_version:
        updates["e2ee_key_version"] = F("e2ee_key_version") + 1
    Conversation.objects.filter(id=conversation.id).update(**updates)
    conversation.refresh_from_db(fields=["e2ee_key_version", "e2ee_rekey_required", "e2ee_last_security_event_at", "updated_at"])
    return conversation


def _mark_user_conversations_e2ee_rekey_required(user, *, bump_version=True):
    now = timezone.now()
    updates = {
        "e2ee_rekey_required": True,
        "e2ee_last_security_event_at": now,
        "updated_at": now,
    }
    if bump_version:
        updates["e2ee_key_version"] = F("e2ee_key_version") + 1
    Conversation.objects.filter(
        participants__user=user,
        participants__left_at__isnull=True,
        participants__banned_at__isnull=True,
        is_active=True,
    ).distinct().update(**updates)


def _clear_conversation_rekey_requirement(conversation):
    now = timezone.now()
    Conversation.objects.filter(id=conversation.id).update(
        e2ee_rekey_required=False,
        e2ee_last_key_rotation_at=now,
        updated_at=now,
    )
    conversation.refresh_from_db(fields=["e2ee_rekey_required", "e2ee_last_key_rotation_at", "updated_at"])
    return conversation


def conversation_has_e2ee_enabled_participants(conversation):
    participant_ids = conversation.participants.filter(left_at__isnull=True, banned_at__isnull=True).values_list("user_id", flat=True)
    return UserE2EEDeviceKey.objects.filter(user_id__in=participant_ids, is_active=True).exists()


def _sanitize_encryption_payload(encryption):
    if not isinstance(encryption, dict):
        raise ValidationError({"encryption": "Encryption envelope must be an object."})
    ciphertext = str(encryption.get("ciphertext") or "")
    if not ciphertext:
        raise ValidationError({"encryption": "Ciphertext is required for encrypted messages."})
    if len(ciphertext.encode("utf-8")) > MESSAGE_MAX_CIPHERTEXT_BYTES:
        raise ValidationError({"encryption": "Ciphertext is too large."})

    recipient_key_ids = [str(value).strip()[:256] for value in encryption.get("recipient_key_ids") or [] if str(value).strip()]
    encrypted_keys = []
    for item in encryption.get("encrypted_keys") or []:
        if not isinstance(item, dict):
            continue
        key_id = str(item.get("key_id") or "").strip()[:256]
        wrapped_key = str(item.get("wrapped_key") or "")
        if not key_id or not wrapped_key:
            continue
        encrypted_keys.append({"key_id": key_id, "wrapped_key": wrapped_key})
    if encrypted_keys and not recipient_key_ids:
        recipient_key_ids = [item["key_id"] for item in encrypted_keys]
    if not recipient_key_ids:
        raise ValidationError({"encryption": "At least one recipient key id is required."})

    sanitized = {
        "version": str(encryption.get("version") or "v1")[:32],
        "algorithm": str(encryption.get("algorithm") or "").strip()[:80],
        "ciphertext": ciphertext,
        "nonce": str(encryption.get("nonce") or "").strip()[:256],
        "sender_key_id": str(encryption.get("sender_key_id") or "").strip()[:256],
        "recipient_key_ids": recipient_key_ids,
    }
    if encrypted_keys:
        sanitized["encrypted_keys"] = encrypted_keys
    if not sanitized["algorithm"]:
        raise ValidationError({"encryption": "Encryption algorithm is required."})
    if not sanitized["nonce"]:
        raise ValidationError({"encryption": "Encryption nonce is required."})
    if not sanitized["sender_key_id"]:
        raise ValidationError({"encryption": "Sender key id is required."})
    if encryption.get("sender_device_id"):
        sanitized["sender_device_id"] = str(encryption.get("sender_device_id")).strip()[:256]
    if encryption.get("key_version") is not None:
        sanitized["key_version"] = max(1, int(encryption.get("key_version")))
    if encryption.get("aad") is not None:
        sanitized["aad"] = encryption.get("aad")
    if _json_payload_size(sanitized) > MESSAGE_MAX_ENCRYPTION_ENVELOPE_BYTES:
        raise ValidationError({"encryption": "Encryption envelope is too large."})
    return sanitized


def _sanitize_attachment_encryption_payloads(payloads):
    sanitized = {}
    for item in payloads or []:
        if not isinstance(item, dict):
            raise ValidationError({"attachment_encryption": "Each attachment encryption payload must be an object."})
        upload_id = str(item.get("upload_id") or "").strip()
        if not upload_id:
            raise ValidationError({"attachment_encryption": "Each attachment encryption payload must include upload_id."})
        recipient_key_ids = [str(value).strip()[:256] for value in item.get("recipient_key_ids") or [] if str(value).strip()]
        encrypted_keys = []
        for key_item in item.get("encrypted_keys") or []:
            if not isinstance(key_item, dict):
                continue
            key_id = str(key_item.get("key_id") or "").strip()[:256]
            wrapped_key = str(key_item.get("wrapped_key") or "")
            if not key_id or not wrapped_key:
                continue
            encrypted_keys.append({"key_id": key_id, "wrapped_key": wrapped_key})
        if encrypted_keys and not recipient_key_ids:
            recipient_key_ids = [entry["key_id"] for entry in encrypted_keys]
        payload = {
            "version": str(item.get("version") or "v1")[:32],
            "algorithm": str(item.get("algorithm") or "").strip()[:80],
            "nonce": str(item.get("nonce") or "").strip()[:256],
            "sender_key_id": str(item.get("sender_key_id") or "").strip()[:256],
            "sender_device_id": str(item.get("sender_device_id") or "").strip()[:256],
            "recipient_key_ids": recipient_key_ids,
            "encrypted_keys": encrypted_keys,
            "metadata_ciphertext": str(item.get("metadata_ciphertext") or ""),
            "metadata_nonce": str(item.get("metadata_nonce") or "").strip()[:256],
            "original_sha256": str(item.get("original_sha256") or "").strip()[:128],
            "preview_ciphertext": str(item.get("preview_ciphertext") or ""),
            "preview_nonce": str(item.get("preview_nonce") or "").strip()[:256],
            "preview_mime_type": str(item.get("preview_mime_type") or "").strip()[:120],
            "aad": item.get("aad") if item.get("aad") is not None else None,
        }
        if item.get("key_version") is not None:
            payload["key_version"] = max(1, int(item.get("key_version")))
        if not payload["algorithm"] or not payload["nonce"] or not payload["sender_key_id"] or not payload["metadata_ciphertext"] or not payload["metadata_nonce"]:
            raise ValidationError({"attachment_encryption": "Attachment encryption payload is incomplete."})
        if not payload["recipient_key_ids"]:
            raise ValidationError({"attachment_encryption": "Attachment encryption payload must include recipient keys."})
        if bool(payload["preview_ciphertext"]) != bool(payload["preview_nonce"]):
            raise ValidationError({"attachment_encryption": "Encrypted attachment previews require both ciphertext and nonce."})
        if payload["preview_ciphertext"] and not payload["preview_mime_type"].lower().startswith("image/"):
            raise ValidationError({"attachment_encryption": "Encrypted attachment previews must use an image MIME type."})
        if _json_payload_size(payload) > MESSAGE_MAX_ENCRYPTION_ENVELOPE_BYTES:
            raise ValidationError({"attachment_encryption": "Attachment encryption payload is too large."})
        sanitized[upload_id] = payload
    return sanitized


def _conversation_e2ee_coverage(conversation):
    participant_ids = list(
        conversation.participants.filter(left_at__isnull=True, banned_at__isnull=True)
        .values_list("user_id", flat=True)
        .distinct()
    )
    key_rows = list(
        UserE2EEDeviceKey.objects.filter(user_id__in=participant_ids, is_active=True)
        .values_list("user_id", "key_id")
    )
    covered_user_ids = {user_id for user_id, _ in key_rows}
    active_key_ids = {key_id for _, key_id in key_rows}
    missing_participant_ids = [user_id for user_id in participant_ids if user_id not in covered_user_ids]
    return active_key_ids, missing_participant_ids


def _validate_conversation_encryption_payload(conversation, payload, *, actor=None, field_name="encryption", label="Encrypted message"):
    active_key_ids, missing_participant_ids = _conversation_e2ee_coverage(conversation)
    if missing_participant_ids:
        raise ValidationError({
            field_name: f"{label} cannot be sent until every participant has a registered secure device.",
            "code": "e2ee_participant_device_missing",
            "missing_participant_ids": [str(value) for value in missing_participant_ids],
        })

    current_key_version = int(conversation.e2ee_key_version or 1)
    envelope_key_version = int(payload.get("key_version") or current_key_version)
    if envelope_key_version != current_key_version:
        raise ValidationError({
            field_name: f"{label} uses an outdated secure-device list. Refresh and try again.",
            "code": "e2ee_stale_key_version",
        })
    payload["key_version"] = envelope_key_version

    recipient_key_ids = set(payload.get("recipient_key_ids") or [])
    wrapped_key_ids = {
        str(item.get("key_id") or "")
        for item in payload.get("encrypted_keys") or []
        if isinstance(item, dict) and item.get("wrapped_key")
    }
    if active_key_ids.difference(recipient_key_ids) or active_key_ids.difference(wrapped_key_ids):
        raise ValidationError({
            field_name: f"{label} does not cover every active secure device.",
            "code": "e2ee_device_coverage_incomplete",
        })

    sender_key_id = str(payload.get("sender_key_id") or "")
    sender_device_id = str(payload.get("sender_device_id") or "")
    sender_keys = UserE2EEDeviceKey.objects.filter(
        user=actor,
        key_id=sender_key_id,
        is_active=True,
    ) if actor is not None else UserE2EEDeviceKey.objects.none()
    if sender_device_id:
        sender_keys = sender_keys.filter(device_id=sender_device_id)
    if actor is not None and not sender_keys.exists():
        raise ValidationError({
            field_name: f"{label} was not created by an active secure device for this account.",
            "code": "e2ee_sender_device_invalid",
        })
    return payload


def upsert_message_transcript(*, actor, message, text="", language_code="", confidence=None, status=MessageTranscript.Status.COMPLETED, source=MessageTranscript.Source.MANUAL):
    ensure_participant(message.conversation, actor)
    if message.type != Message.MessageType.AUDIO and not (message.metadata or {}).get("voice_note"):
        raise ValidationError({"message": "Transcript is supported only for audio or voice-note messages."})
    transcript, _ = MessageTranscript.objects.get_or_create(message=message)
    transcript.text = text or ""
    transcript.language_code = (language_code or "")[:16]
    transcript.status = status or MessageTranscript.Status.COMPLETED
    transcript.source = source or MessageTranscript.Source.MANUAL
    transcript.confidence = confidence
    transcript.save()
    metadata = dict(message.metadata or {})
    metadata["has_transcript"] = bool(transcript.text)
    if transcript.language_code:
        metadata["transcript_language_code"] = transcript.language_code
    message.metadata = metadata
    message.save(update_fields=["metadata", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.MESSAGE_EDITED, actor=actor, conversation=message.conversation, message=message, metadata={"transcript_updated": True})
    return transcript
def build_call_recovery_plan(call):
    participants = list(call.participants.all())
    if not participants:
        return {"action": "keep", "reason": "no_participants", "target_quality": "high", "max_remote_streams": 1, "audio_only": False}
    worst = min((p.quality_score for p in participants), default=100)
    offline_count = sum(1 for p in participants if p.network_quality == CallParticipant.NetworkQuality.OFFLINE)
    poor_count = sum(1 for p in participants if p.network_quality in {CallParticipant.NetworkQuality.POOR, CallParticipant.NetworkQuality.OFFLINE})
    joined_count = sum(1 for p in participants if p.state == CallParticipant.State.JOINED)
    if offline_count:
        return {"action": "restart_ice", "reason": "participant_offline", "target_quality": "low", "max_remote_streams": 1, "audio_only": joined_count > 2}
    if worst < 25:
        return {"action": "audio_only", "reason": "critical_quality", "target_quality": "off", "max_remote_streams": 0, "audio_only": True}
    if worst < 45 or poor_count:
        return {"action": "reduce_video", "reason": "degraded_quality", "target_quality": "low", "max_remote_streams": 1 if joined_count > 2 else max(joined_count, 1), "audio_only": False}
    if worst < 70:
        return {"action": "limit_streams", "reason": "fair_quality", "target_quality": "medium", "max_remote_streams": min(2, max(joined_count, 1)), "audio_only": False}
    return {"action": "keep", "reason": "healthy_quality", "target_quality": "high", "max_remote_streams": min(4, max(joined_count, 1)), "audio_only": False}


def get_call_quality_summary(call):
    participants = list(call.participants.all())
    if not participants:
        return {"min_score": 100, "avg_score": 100, "degraded_user_ids": [], "offline_user_ids": []}
    scores = [int(p.quality_score or 0) for p in participants]
    return {
        "min_score": min(scores),
        "avg_score": int(round(sum(scores) / len(scores))),
        "degraded_user_ids": [str(p.user_id) for p in participants if (p.quality_score or 0) < 70],
        "offline_user_ids": [str(p.user_id) for p in participants if p.network_quality == CallParticipant.NetworkQuality.OFFLINE],
    }


def _direct_key_for_users(user_a_id, user_b_id):
    first, second = sorted([str(user_a_id), str(user_b_id)])
    return f"{first}:{second}"


def log_chat_event(event_type, *, actor=None, conversation=None, message=None, metadata=None):
    ChatAuditLog.objects.create(
        actor=actor,
        conversation=conversation,
        message=message,
        event_type=event_type,
        metadata=metadata or {},
    )


def _active_participant(conversation, user):
    return ConversationParticipant.objects.filter(conversation=conversation, user=user, left_at__isnull=True).first()


def ensure_participant(conversation, user):
    participant = _active_participant(conversation, user)
    if not participant:
        raise PermissionDenied("You are not a participant of this conversation.")
    if participant.is_blocked:
        raise PermissionDenied("Your participation in this conversation is restricted.")
    return participant


def ensure_group_admin(conversation, user):
    participant = ensure_participant(conversation, user)
    if conversation.type != Conversation.ConversationType.GROUP:
        raise ValidationError({"conversation": "Participant management is supported only for group conversations."})
    if participant.role not in {ConversationParticipant.Role.ADMIN, ConversationParticipant.Role.OWNER}:
        raise PermissionDenied("Only group admins can manage participants.")
    return participant


def ensure_group_owner(conversation, user):
    participant = ensure_participant(conversation, user)
    if conversation.type != Conversation.ConversationType.GROUP:
        raise ValidationError({"conversation": "Ownership management is supported only for group conversations."})
    if participant.role != ConversationParticipant.Role.OWNER:
        raise PermissionDenied("Only the group owner can perform this action.")
    return participant


def ensure_participant_can_send(conversation, user):
    participant = ensure_participant(conversation, user)
    now = timezone.now()
    if participant.banned_at:
        raise PermissionDenied("You are banned from participating in this conversation.")
    if participant.moderation_muted_until and participant.moderation_muted_until > now:
        raise PermissionDenied("You are temporarily muted in this conversation.")
    return participant


def has_block_relationship(user_a, user_b):
    return UserBlock.objects.filter(Q(blocker=user_a, blocked=user_b) | Q(blocker=user_b, blocked=user_a)).exists()


def get_webrtc_ice_servers():
    raw = getattr(settings, "WEBRTC_ICE_SERVERS_JSON", "").strip()
    if raw:
        try:
            payload = json.loads(raw)
            if isinstance(payload, list):
                return payload
        except json.JSONDecodeError:
            logger.warning("Invalid WEBRTC_ICE_SERVERS_JSON; falling back to default STUN server.")
    return [{"urls": ["stun:stun.l.google.com:19302"]}]


def get_available_call_quality_presets():
    return {
        "auto": {
            "label": "Auto",
            "description": "Adapts quality automatically based on network conditions.",
            "video": {"mode": "adaptive"},
            "audio": {"mode": "adaptive"},
        },
        "low": {
            "label": "Low",
            "description": "Uses less data and is better for weak networks.",
            "video": {"max_bitrate_bps": 180000, "max_framerate": 10, "max_width": 240, "max_height": 180},
            "audio": {"max_average_bitrate_bps": 20000, "opus_dtx": True, "opus_fec": True},
        },
        "mid": {
            "label": "Mid",
            "description": "Balanced clarity and data usage.",
            "video": {"max_bitrate_bps": 450000, "max_framerate": 18, "max_width": 640, "max_height": 360},
            "audio": {"max_average_bitrate_bps": 28000, "opus_dtx": True, "opus_fec": True},
        },
        "clear": {
            "label": "Clear",
            "description": "Prioritizes the clearest call quality and uses more bandwidth.",
            "video": {"max_bitrate_bps": 1200000, "max_framerate": 30, "max_width": 1280, "max_height": 720},
            "audio": {"max_average_bitrate_bps": 40000, "opus_dtx": False, "opus_fec": True},
        },
    }


def resolve_call_quality_preset(actor=None, requested_preset=None):
    presets = get_available_call_quality_presets()
    selected = (requested_preset or "").strip().lower()
    if not selected and actor is not None and getattr(actor, "is_authenticated", False):
        preference = getattr(actor, "notification_preference", None)
        if preference and preference.call_quality_preference in presets:
            selected = preference.call_quality_preference
    if selected not in presets:
        selected = NotificationPreference.CallQualityPreference.AUTO
    return selected, presets[selected]


def get_calling_config(actor=None, requested_preset=None):
    low_bandwidth_video_profile = {
        "max_bitrate_bps": int(getattr(settings, "LOW_BANDWIDTH_VIDEO_MAX_BITRATE_BPS", 250000) or 250000),
        "max_framerate": int(getattr(settings, "LOW_BANDWIDTH_VIDEO_MAX_FRAMERATE", 12) or 12),
        "max_width": int(getattr(settings, "LOW_BANDWIDTH_VIDEO_MAX_WIDTH", 320) or 320),
        "max_height": int(getattr(settings, "LOW_BANDWIDTH_VIDEO_MAX_HEIGHT", 240) or 240),
    }
    audio_fallback_profile = {
        "opus_dtx": True,
        "opus_fec": True,
        "max_average_bitrate_bps": int(getattr(settings, "AUDIO_FALLBACK_MAX_BITRATE_BPS", 24000) or 24000),
    }
    selected_quality_preset, applied_quality_profile = resolve_call_quality_preset(actor=actor, requested_preset=requested_preset)
    return {
        "ice_servers": get_webrtc_ice_servers(),
        "offer_timeout_seconds": int(getattr(settings, "CALL_OFFER_TIMEOUT_SECONDS", 45) or 45),
        "max_group_call_participants": int(getattr(settings, "MAX_GROUP_CALL_PARTICIPANTS", 8) or 8),
        "ice_transport_policy": getattr(settings, "WEBRTC_ICE_TRANSPORT_POLICY", "all"),
        "ice_candidate_pool_size": int(getattr(settings, "WEBRTC_ICE_CANDIDATE_POOL_SIZE", 4) or 4),
        "enable_simulcast": bool(int(getattr(settings, "WEBRTC_ENABLE_SIMULCAST", 1) or 1)),
        "prefer_audio_only_below_quality": getattr(settings, "CALL_AUDIO_ONLY_NETWORK_THRESHOLD", "poor"),
        "reconnect_grace_seconds": int(getattr(settings, "CALL_RECONNECT_GRACE_SECONDS", 20) or 20),
        "quality_report_interval_seconds": int(getattr(settings, "CALL_QUALITY_REPORT_INTERVAL_SECONDS", 5) or 5),
        "dominant_speaker_hold_ms": int(getattr(settings, "CALL_DOMINANT_SPEAKER_HOLD_MS", 2500) or 2500),
        "speaker_level_threshold": int(getattr(settings, "CALL_SPEAKER_LEVEL_THRESHOLD", 35) or 35),
        "grid_layout_threshold": int(getattr(settings, "CALL_GRID_LAYOUT_THRESHOLD", 4) or 4),
        "supported_audio_routes": [choice for choice, _ in CallParticipant.AudioRoute.choices],
        "screen_share": {
            "allow_simultaneous_screen_shares": bool(int(getattr(settings, "CALL_ALLOW_SIMULTANEOUS_SCREEN_SHARES", 0) or 0)),
            "prioritize_presenter_layout": True,
        },
        "network_profiles": {
            "low_bandwidth_video": low_bandwidth_video_profile,
            "audio_fallback": audio_fallback_profile,
            "reconnect_profile": {
                "ice_restart_backoff_ms": int(getattr(settings, "CALL_ICE_RESTART_BACKOFF_MS", 1500) or 1500),
                "max_ice_restarts": int(getattr(settings, "CALL_MAX_ICE_RESTARTS", 4) or 4),
                "heartbeat_interval_seconds": int(getattr(settings, "CALL_HEARTBEAT_INTERVAL_SECONDS", 10) or 10),
                "stale_participant_seconds": int(getattr(settings, "CALL_STALE_PARTICIPANT_SECONDS", 35) or 35),
            },
        },
        "codec_preferences": {
            "audio": ["opus", "pcmu"],
            "video": ["vp9", "vp8", "h264"],
            "enable_opus_dtx": True,
            "enable_opus_fec": True,
        },
        "available_quality_presets": get_available_call_quality_presets(),
        "selected_quality_preset": selected_quality_preset,
        "applied_quality_profile": applied_quality_profile,
        "quality_reporting": {
            "enabled": True,
            "interval_seconds": int(getattr(settings, "CALL_QUALITY_REPORT_INTERVAL_SECONDS", 5) or 5),
            "stale_participant_seconds": int(getattr(settings, "CALL_STALE_PARTICIPANT_SECONDS", 35) or 35),
            "dominant_speaker_hold_ms": int(getattr(settings, "CALL_DOMINANT_SPEAKER_HOLD_MS", 2500) or 2500),
        },
    }


def get_active_call_for_conversation(conversation):
    for stale_call in CallSession.objects.filter(
        conversation=conversation,
        status__in=[CallSession.Status.INITIATED, CallSession.Status.RINGING],
        started_at__lt=_ring_timeout_cutoff(),
    ).select_related("conversation", "initiated_by").prefetch_related("participants__user"):
        expire_ringing_call(stale_call)
    return CallSession.objects.filter(
        conversation=conversation,
        status__in=ACTIVE_CALL_STATUSES,
    ).select_related("initiated_by", "answered_by").prefetch_related("participants__user").first()


def _ring_timeout_cutoff():
    timeout_seconds = int(get_calling_config()["offer_timeout_seconds"])
    return timezone.now() - timedelta(seconds=timeout_seconds)

def update_call_participant_network_state(actor, call, *, network_quality=None, preferred_video_quality=None, audio_enabled=None, video_enabled=None, quality_payload=None):
    participant = CallParticipant.objects.filter(call=call, user=actor).first()
    if not participant:
        raise PermissionDenied("You are not a participant of this call.")
    if call.status not in {CallSession.Status.RINGING, CallSession.Status.ONGOING, CallSession.Status.INITIATED}:
        raise ValidationError({"call": "Network state can only be updated for active or ringing calls."})

    changed = False
    now = timezone.now()
    if network_quality and network_quality in CallParticipant.NetworkQuality.values:
        participant.network_quality = network_quality
        changed = True
    if preferred_video_quality and preferred_video_quality in CallParticipant.VideoPreference.values:
        participant.preferred_video_quality = preferred_video_quality
        changed = True
    if audio_enabled is not None:
        participant.audio_enabled = bool(audio_enabled)
        changed = True
    if video_enabled is not None:
        participant.video_enabled = bool(video_enabled)
        changed = True
    participant.last_quality_report_at = now
    participant.last_seen_signal_at = now
    participant.last_heartbeat_at = now
    if quality_payload:
        participant.diagnostics = quality_payload
    participant.save(update_fields=[
        "network_quality",
        "preferred_video_quality",
        "audio_enabled",
        "video_enabled",
        "last_quality_report_at",
        "last_seen_signal_at",
        "last_heartbeat_at",
        "diagnostics",
        "updated_at",
    ])
    metadata = dict(call.metadata or {})
    if quality_payload:
        reports = metadata.get("quality_reports") or {}
        reports[str(actor.id)] = {
            "network_quality": participant.network_quality,
            "preferred_video_quality": participant.preferred_video_quality,
            "audio_enabled": participant.audio_enabled,
            "video_enabled": participant.video_enabled,
            "reported_at": now.isoformat(),
            "metrics": quality_payload,
        }
        metadata["quality_reports"] = reports
        call.metadata = metadata
        call.last_signal_at = now
        call.save(update_fields=["metadata", "last_signal_at", "updated_at"])
    elif changed:
        call.last_signal_at = now
        call.save(update_fields=["last_signal_at", "updated_at"])
    return participant


def get_call_network_recommendation(call):
    participants = list(call.participants.all())
    if not participants:
        return {"mode": "standard", "reason": "no_participants"}

    if call.status in {CallSession.Status.INITIATED, CallSession.Status.RINGING}:
        return {"mode": "standard", "reason": "awaiting_answer"}

    joined_participants = [p for p in participants if p.state == CallParticipant.State.JOINED]
    if not joined_participants:
        return {"mode": "standard", "reason": "awaiting_participants"}

    if any(p.network_quality == CallParticipant.NetworkQuality.OFFLINE for p in joined_participants):
        return {"mode": "reconnect", "reason": "participant_offline"}

    quality_order = {
        CallParticipant.NetworkQuality.EXCELLENT: 4,
        CallParticipant.NetworkQuality.GOOD: 3,
        CallParticipant.NetworkQuality.FAIR: 2,
        CallParticipant.NetworkQuality.POOR: 1,
    }
    known_levels = [quality_order[p.network_quality] for p in joined_participants if p.network_quality in quality_order]
    if not known_levels:
        return {"mode": "standard", "reason": "awaiting_quality_signal"}

    lowest = min(known_levels)
    if lowest == 1:
        if any(
            p.network_quality == CallParticipant.NetworkQuality.POOR and not p.video_enabled
            for p in joined_participants
        ):
            return {"mode": "audio_only", "reason": "poor_network_video_disabled"}
        if len(joined_participants) <= 2:
            return {"mode": "low_bandwidth_video", "reason": "poor_network_1to1"}
        return {"mode": "audio_only", "reason": "poor_network"}
    if lowest == 2:
        return {"mode": "low_bandwidth_video", "reason": "fair_network"}
    return {"mode": "standard", "reason": "healthy_network"}


def get_call_orchestration(call, recipient=None, *, consume_signals=True):
    participants = list(call.participants.select_related("user"))
    recommendation = get_call_network_recommendation(call)
    speaking = [p for p in participants if p.is_speaking and p.state == CallParticipant.State.JOINED]
    screen_sharers = [p for p in participants if p.screen_share_enabled and p.state == CallParticipant.State.JOINED]
    raised_hands = [p for p in participants if p.raised_hand_at and p.state == CallParticipant.State.JOINED]
    active_speaker = None
    if speaking:
        active_speaker = max(speaking, key=lambda p: (p.speaking_level, p.last_spoke_at or p.joined_at or p.invited_at))
    joined_count = sum(1 for p in participants if p.state == CallParticipant.State.JOINED)
    grid_threshold = int(getattr(settings, "CALL_GRID_LAYOUT_THRESHOLD", 4) or 4)
    layout_mode = "grid"
    if recommendation["mode"] == "audio_only":
        layout_mode = "audio_only"
    elif screen_sharers:
        layout_mode = "presentation"
    elif joined_count <= 2:
        layout_mode = "focused"
    elif active_speaker and joined_count < grid_threshold:
        layout_mode = "speaker_focus"
    recommended_quality = "high"
    recommended_max_streams = max(1, min(joined_count or 1, grid_threshold))
    recommend_audio_only = False
    if recommendation["mode"] == "audio_only":
        recommended_quality = "off"
        recommended_max_streams = 0
        recommend_audio_only = True
    elif recommendation["mode"] == "low_bandwidth_video":
        recommended_quality = "low"
        recommended_max_streams = 2 if joined_count > 2 else 1
    elif recommendation["mode"] == "reconnect":
        recommended_quality = "low"
        recommended_max_streams = 1
    participant_payload = []
    for p in participants:
        participant_payload.append({
            "user_id": str(p.user_id),
            "state": p.state,
            "network_quality": p.network_quality,
            "is_speaking": p.is_speaking,
            "speaking_level": p.speaking_level,
            "video_enabled": p.video_enabled,
            "audio_enabled": p.audio_enabled,
            "preferred_video_quality": p.preferred_video_quality,
            "connection_state": p.connection_state,
            "audio_route": p.audio_route,
            "screen_share_enabled": p.screen_share_enabled,
            "raised_hand_at": p.raised_hand_at.isoformat() if p.raised_hand_at else None,
        })
    primary_content = screen_sharers[0] if screen_sharers else None
    raised_hands_sorted = [str(p.user_id) for p in sorted(raised_hands, key=lambda p: p.raised_hand_at or p.invited_at)]
    recovery_plan = build_call_recovery_plan(call)
    payload = {
        "call_id": str(call.id),
        "conversation_id": str(call.conversation_id),
        "active_speaker_user_id": str(active_speaker.user_id) if active_speaker else None,
        "primary_content_user_id": str(primary_content.user_id) if primary_content else None,
        "layout_mode": layout_mode,
        "network_recommendation": recommendation,
        "recommended_video_quality": recommended_quality,
        "recommended_max_video_streams": recommended_max_streams,
        "recommend_audio_only": recommend_audio_only,
        "recovery_plan": recovery_plan,
        "participant_speaking_user_ids": [str(p.user_id) for p in speaking],
        "raised_hand_user_ids": raised_hands_sorted,
        "generated_at": timezone.now().isoformat(),
        "participants": participant_payload,
    }
    if recipient is not None:
        recipient_id = str(getattr(recipient, "id", recipient))
        pending_signals = _pop_pending_call_signals(call.id, recipient_id) if consume_signals else (cache.get(_call_signal_cache_key(call.id, recipient_id)) or [])
        if pending_signals:
            payload["signals"] = pending_signals
    metadata = dict(call.metadata or {})
    persisted_orchestration = {key: value for key, value in payload.items() if key not in {"signals", "pending_signals", "queued_signals", "call_signals"}}
    metadata["orchestration"] = persisted_orchestration
    call.metadata = metadata
    call.save(update_fields=["metadata", "updated_at"])
    return payload


def get_turn_credentials(actor=None):
    ttl = int(getattr(settings, "TURN_CREDENTIAL_TTL_SECONDS", 3600) or 3600)
    uris_raw = getattr(settings, "TURN_URIS_JSON", "").strip()
    uris = []
    if uris_raw:
        try:
            loaded = json.loads(uris_raw)
            if isinstance(loaded, list):
                uris = loaded
        except json.JSONDecodeError:
            logger.warning("Invalid TURN_URIS_JSON; skipping TURN credential payload.")
    if not uris:
        return {"configured": False, "ttl_seconds": ttl, "ice_servers": []}

    static_username = getattr(settings, "TURN_STATIC_USERNAME", "").strip()
    static_password = getattr(settings, "TURN_STATIC_PASSWORD", "").strip()
    shared_secret = getattr(settings, "TURN_SHARED_SECRET", "").strip()
    now_ts = int(timezone.now().timestamp())
    username_hint = str(getattr(actor, "id", "anonymous"))
    if shared_secret:
        import base64, hashlib, hmac
        expiry = now_ts + ttl
        username = f"{expiry}:{username_hint}"
        credential = base64.b64encode(hmac.new(shared_secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
        return {
            "configured": True,
            "ttl_seconds": ttl,
            "username": username,
            "credential": credential,
            "credential_type": "password",
            "ice_servers": [{"urls": uris, "username": username, "credential": credential}],
        }
    if static_username and static_password:
        return {
            "configured": True,
            "ttl_seconds": ttl,
            "username": static_username,
            "credential": static_password,
            "credential_type": "password",
            "ice_servers": [{"urls": uris, "username": static_username, "credential": static_password}],
        }
    return {"configured": False, "ttl_seconds": ttl, "ice_servers": []}


def get_call_diagnostics(call):
    participants = list(call.participants.select_related("user", "user__profile"))
    now = timezone.now()
    stale_seconds = int(getattr(settings, "CALL_STALE_PARTICIPANT_SECONDS", 35) or 35)
    stale_cutoff = now - timedelta(seconds=stale_seconds)
    stale = []
    active = joined = 0
    for participant in participants:
        if participant.state in {CallParticipant.State.JOINED, CallParticipant.State.RINGING}:
            active += 1
        if participant.state == CallParticipant.State.JOINED:
            joined += 1
        if participant.last_heartbeat_at and participant.last_heartbeat_at < stale_cutoff and participant.state == CallParticipant.State.JOINED:
            stale.append(str(participant.user_id))
    return {
        "call_id": str(call.id),
        "status": call.status,
        "participant_count": len(participants),
        "joined_count": joined,
        "active_count": active,
        "stale_participant_user_ids": stale,
        "network_recommendation": get_call_network_recommendation(call),
        "recovery_plan": build_call_recovery_plan(call),
        "aggregate_quality": get_call_quality_summary(call),
        "orchestration": get_call_orchestration(call),
        "last_signal_at": call.last_signal_at.isoformat() if call.last_signal_at else None,
        "participants": [
            {
                "user_id": str(p.user_id),
                "state": p.state,
                "network_quality": p.network_quality,
                "preferred_video_quality": p.preferred_video_quality,
                "audio_enabled": p.audio_enabled,
                "video_enabled": p.video_enabled,
                "is_on_hold": p.is_on_hold,
                "reconnecting": p.reconnecting,
                "connection_state": p.connection_state,
                "audio_route": p.audio_route,
                "screen_share_enabled": p.screen_share_enabled,
                "screen_share_started_at": p.screen_share_started_at.isoformat() if p.screen_share_started_at else None,
                "raised_hand_at": p.raised_hand_at.isoformat() if p.raised_hand_at else None,
                "last_heartbeat_at": p.last_heartbeat_at.isoformat() if p.last_heartbeat_at else None,
                "last_seen_signal_at": p.last_seen_signal_at.isoformat() if p.last_seen_signal_at else None,
                "packet_loss_pct": float(p.packet_loss_pct) if p.packet_loss_pct is not None else None,
                "jitter_ms": p.jitter_ms,
                "round_trip_time_ms": p.round_trip_time_ms,
                "bitrate_kbps": p.bitrate_kbps,
                "frame_rate": p.frame_rate,
                "quality_score": p.quality_score,
                "quality_alert": p.quality_alert,
                "diagnostics": p.diagnostics or {},
            }
            for p in participants
        ],
    }


def _presence_user_key(user_id):
    return f"presence:user:{user_id}:devices"


def _presence_lock_key(user_id):
    return f"presence:user:{user_id}:lock"


@contextmanager
def _presence_cache_lock(user_id):
    """Best-effort cross-worker lock for multi-device presence registry updates."""
    lock_key = _presence_lock_key(user_id)
    token = uuid4().hex
    acquired = False
    for _ in range(20):
        if cache.add(lock_key, token, timeout=2):
            acquired = True
            break
        time.sleep(0.01)
    try:
        yield
    finally:
        if acquired and cache.get(lock_key) == token:
            cache.delete(lock_key)


def _active_presence_devices(user_id, *, now_ts=None):
    ttl = max(int(getattr(settings, "PRESENCE_TTL_SECONDS", 75) or 75), 15)
    now_ts = float(now_ts or timezone.now().timestamp())
    raw = cache.get(_presence_user_key(user_id)) or {}
    registry = raw if isinstance(raw, dict) else {}
    active = {
        str(device_id): float(last_seen)
        for device_id, last_seen in registry.items()
        if str(device_id) and isinstance(last_seen, (int, float)) and now_ts - float(last_seen) < ttl
    }
    if active != registry:
        if active:
            cache.set(_presence_user_key(user_id), active, timeout=ttl * 2)
        else:
            cache.delete(_presence_user_key(user_id))
    return active


def get_presence_snapshot(user_id):
    active_devices = _active_presence_devices(user_id)
    return {"is_online": bool(active_devices), "active_devices": len(active_devices)}


def is_user_online(user_id):
    return get_presence_snapshot(user_id)["is_online"]


def set_presence(user, device_id="default"):
    ttl = max(int(getattr(settings, "PRESENCE_TTL_SECONDS", 75) or 75), 15)
    device_id = str(device_id or "default")[:160]
    now = timezone.now()
    with _presence_cache_lock(user.id):
        registry = _active_presence_devices(user.id, now_ts=now.timestamp())
        registry[device_id] = now.timestamp()
        cache.set(_presence_user_key(user.id), registry, timeout=ttl * 2)
    type(user).objects.filter(id=user.id).update(last_seen_at=now)
    return {"is_online": True, "active_devices": len(registry)}


def clear_presence(user, device_id="default"):
    ttl = max(int(getattr(settings, "PRESENCE_TTL_SECONDS", 75) or 75), 15)
    device_id = str(device_id or "default")[:160]
    now = timezone.now()
    with _presence_cache_lock(user.id):
        registry = _active_presence_devices(user.id, now_ts=now.timestamp())
        registry.pop(device_id, None)
        if registry:
            cache.set(_presence_user_key(user.id), registry, timeout=ttl * 2)
        else:
            cache.delete(_presence_user_key(user.id))
    type(user).objects.filter(id=user.id).update(last_seen_at=now)
    return {"is_online": bool(registry), "active_devices": len(registry)}


def get_public_presence_snapshot(user, snapshot=None):
    """Return presence safe to expose to another account."""
    current = (
        type(user).objects.select_related("profile")
        .only("id", "last_seen_at", "profile__show_online_status")
        .filter(id=user.id, is_active=True)
        .first()
    )
    if current is None:
        return {
            "is_online": False,
            "active_devices": 0,
            "last_seen_at": None,
            "presence_label": "offline",
            "visibility": "hidden",
        }
    profile = getattr(current, "profile", None)
    if profile is not None and not getattr(profile, "show_online_status", True):
        return {
            "is_online": False,
            "active_devices": 0,
            "last_seen_at": None,
            "presence_label": "offline",
            "visibility": "hidden",
        }
    resolved = snapshot or get_presence_snapshot(current.id)
    online = bool(resolved.get("is_online"))
    return {
        "is_online": online,
        "active_devices": int(resolved.get("active_devices") or 0),
        "last_seen_at": current.last_seen_at.isoformat() if current.last_seen_at else None,
        "presence_label": "online" if online else "offline",
        "visibility": "public",
    }


def presence_recipient_ids(user):
    """Return active, unblocked accounts allowed to receive this user's presence."""
    from apps.accounts.models import FriendRequest

    active_conversation_ids = ConversationParticipant.objects.filter(
        user=user,
        conversation__is_active=True,
        left_at__isnull=True,
        banned_at__isnull=True,
    ).values_list("conversation_id", flat=True)
    user_ids = {
        user_id
        for user_id in ConversationParticipant.objects.filter(
            conversation_id__in=active_conversation_ids,
            left_at__isnull=True,
            banned_at__isnull=True,
            user__is_active=True,
        )
        .exclude(user_id=user.id)
        .values_list("user_id", flat=True)
        .distinct()
    }
    friendships = FriendRequest.objects.filter(status=FriendRequest.Status.ACCEPTED).filter(
        Q(sender=user) | Q(receiver=user)
    ).values_list("sender_id", "receiver_id")
    for sender_id, receiver_id in friendships:
        user_ids.add(receiver_id if sender_id == user.id else sender_id)

    blocked_ids = set(UserBlock.objects.filter(blocker=user).values_list("blocked_id", flat=True))
    blocked_ids.update(UserBlock.objects.filter(blocked=user).values_list("blocker_id", flat=True))
    return sorted(str(user_id) for user_id in user_ids if user_id not in blocked_ids)


def request_user_devices(user):
    return UserDevice.objects.filter(user=user).order_by("-last_seen_at", "-created_at")


def get_notification_preference(user):
    preference, _ = NotificationPreference.objects.get_or_create(user=user)
    return preference


def dispatch_message_notifications(message):
    from .tasks import fanout_push_notifications

    def enqueue_push_notifications():
        try:
            fanout_push_notifications.delay(str(message.id))
        except Exception as exc:
            logger.warning("Push notification enqueue failed for message %s: %s", message.id, exc)

    transaction.on_commit(enqueue_push_notifications)


def dispatch_call_notifications(call):
    from .tasks import fanout_incoming_call_notifications

    def enqueue_call_notifications():
        try:
            fanout_incoming_call_notifications.delay(str(call.id))
        except Exception as exc:
            logger.warning("Call notification enqueue failed for call %s: %s", call.id, exc)

    transaction.on_commit(enqueue_call_notifications)


def dispatch_pending_upload_scan(upload):
    from .tasks import scan_pending_upload

    def enqueue_pending_upload_scan():
        try:
            scan_pending_upload.delay(str(upload.id))
        except Exception as exc:
            logger.warning("Pending upload scan enqueue failed for %s: %s", upload.id, exc)
            scan_upload_file(upload)

    transaction.on_commit(enqueue_pending_upload_scan)


def _detect_forbidden_upload(upload, initial_bytes=None):
    name = (upload.original_name or "").lower()
    mime = (upload.mime_type or mimetypes.guess_type(name)[0] or "").lower()
    blocked_extensions = {"exe", "bat", "cmd", "com", "scr", "ps1", "sh", "msi", "jar", "dll"}
    blocked_mimes = {"application/x-msdownload", "application/x-dosexec", "application/x-sh"}
    if upload.extension in blocked_extensions:
        return "Executable uploads are not allowed."
    if mime in blocked_mimes or "x-msdownload" in mime or "x-sh" in mime:
        return "Upload blocked by mime policy."
    if initial_bytes is not None:
        header = initial_bytes[:16]
    else:
        try:
            upload.file.open("rb")
            header = upload.file.read(16)
            upload.file.close()
        except Exception:
            header = b""
    if header.startswith(b"MZ"):
        return "Portable executable signatures are not allowed."
    if b"#!/bin/sh" in header or b"#!/bin/bash" in header:
        return "Shell script signatures are not allowed."
    return ""


def expire_pending_upload_if_needed(upload):
    if upload.status == PendingUpload.UploadStatus.PENDING and upload.expires_at and upload.expires_at <= timezone.now():
        upload.status = PendingUpload.UploadStatus.EXPIRED
        upload.scan_notes = upload.scan_notes or "Pending upload expired before use."
        upload.save(update_fields=["status", "scan_notes", "updated_at"])
        _safe_delete_filefield(upload.thumbnail)
    return upload


def scan_upload_file(upload, initial_bytes=None):
    upload = expire_pending_upload_if_needed(upload)
    if upload.status == PendingUpload.UploadStatus.EXPIRED:
        return upload
    reason = _detect_forbidden_upload(upload, initial_bytes=initial_bytes)
    if reason:
        upload.scan_status = PendingUpload.ScanStatus.INFECTED
        upload.status = PendingUpload.UploadStatus.REJECTED
        upload.scan_notes = reason
        _safe_delete_filefield(upload.thumbnail)
    else:
        verdict = scan_file_field(upload.file, initial_bytes=initial_bytes)
        if verdict.is_clean:
            upload.scan_status = PendingUpload.ScanStatus.CLEAN
            upload.scan_notes = f"{verdict.engine}: {verdict.notes}"
        elif verdict.status == "failed":
            upload.scan_status = PendingUpload.ScanStatus.FAILED
            upload.status = PendingUpload.UploadStatus.REJECTED
            upload.scan_notes = f"{verdict.engine}: {verdict.notes}"
            _safe_delete_filefield(upload.thumbnail)
        else:
            upload.scan_status = PendingUpload.ScanStatus.INFECTED
            upload.status = PendingUpload.UploadStatus.REJECTED
            upload.scan_notes = f"{verdict.engine}: {verdict.notes}"
            _safe_delete_filefield(upload.thumbnail)
    upload.scanned_at = timezone.now()
    upload.save(update_fields=["scan_status", "status", "scan_notes", "scanned_at", "updated_at"])
    if upload.scan_status == PendingUpload.ScanStatus.CLEAN and upload.status == PendingUpload.UploadStatus.PENDING:
        enrich_pending_upload_media(upload)
    log_chat_event(
        ChatAuditLog.EventType.UPLOAD_SCANNED,
        actor=upload.user,
        metadata={
            "upload_id": str(upload.id),
            "scan_status": upload.scan_status,
            "status": upload.status,
            "reason": upload.scan_notes,
        },
    )
    return upload




def make_realtime_event(event_name, data, *, event_id=None, occurred_at=None):
    """Build a stable, deduplicatable websocket event envelope."""
    return make_realtime_safe({
        "type": "chat.event",
        "event": str(event_name),
        "event_id": str(event_id or uuid4().hex),
        "occurred_at": occurred_at or timezone.now().isoformat(),
        "data": data or {},
    })


def make_realtime_safe(value):
    """Convert non-msgpack-safe objects into websocket/cache-safe primitives."""
    from datetime import date, datetime
    from decimal import Decimal
    from uuid import UUID

    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): make_realtime_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_realtime_safe(v) for v in value]
    return value

def build_media_access_token(*, resource_type, resource_id, user_id=None, purpose="standard"):
    payload = {
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "user_id": str(user_id) if user_id else None,
        "issued_at": timezone.now().isoformat(),
        "purpose": purpose,
    }
    token = signing.dumps(payload, salt=MEDIA_TOKEN_SALT)
    return token


def validate_media_access_token(token, *, resource_type, resource_id, user=None):
    max_age = int(getattr(settings, "MEDIA_TOKEN_TTL_SECONDS", 300) or 300)
    try:
        payload = signing.loads(token, salt=MEDIA_TOKEN_SALT, max_age=max_age)
    except signing.BadSignature:
        raise PermissionDenied("Invalid or expired media token.")
    if payload.get("resource_type") != resource_type or str(payload.get("resource_id")) != str(resource_id):
        raise PermissionDenied("Media token does not match requested resource.")
    expected_user_id = payload.get("user_id")
    # Media tokens are short-lived bearer capabilities so native <video> and
    # <audio> elements can stream and seek without exposing the account JWT in
    # a URL. When an authenticated identity is present, still reject a token
    # issued for a different account.
    if expected_user_id and user and getattr(user, "is_authenticated", False):
        if str(user.id) != str(expected_user_id):
            raise PermissionDenied("Media token does not belong to this user.")
    return payload


def create_media_access_payload(*, actor, resource_type, resource_id, request=None, disposition="attachment", purpose="standard"):
    """Build signed media access metadata for preview/download use cases.

    Supported resource types: attachment, pending_upload.
    Supported dispositions: attachment, inline.
    """
    normalized_resource_type = "pending_upload" if resource_type in {"pending_upload", "upload"} else resource_type
    normalized_disposition = "inline" if disposition == "inline" else "attachment"

    token = build_media_access_token(
        resource_type=normalized_resource_type,
        resource_id=resource_id,
        user_id=actor.id if actor else None,
        purpose=purpose,
    )

    if normalized_resource_type == "attachment":
        base_path = f"/api/v1/chat/attachments/{resource_id}"
    elif normalized_resource_type == "pending_upload":
        base_path = f"/api/v1/chat/uploads/{resource_id}"
    else:
        raise ValueError(f"Unsupported media resource_type: {resource_type}")

    action = "preview" if normalized_disposition == "inline" else "download"
    primary_path = f"{base_path}/{action}/?token={token}"
    preview_path = f"{base_path}/preview/?token={token}"
    download_path = f"{base_path}/download/?token={token}"

    def _abs(path: str):
        return request.build_absolute_uri(path) if request else path

    payload = {
        "token": token,
        "expires_in": int(getattr(settings, "MEDIA_TOKEN_TTL_SECONDS", 300) or 300),
        "url": _abs(primary_path),
        "download_url": _abs(download_path),
        "preview_url": _abs(preview_path),
        "disposition": normalized_disposition,
        "resource_type": normalized_resource_type,
        "resource_id": str(resource_id),
    }
    log_chat_event(
        ChatAuditLog.EventType.MEDIA_TOKEN_ISSUED,
        actor=actor,
        metadata={
            "resource_type": normalized_resource_type,
            "resource_id": str(resource_id),
            "disposition": normalized_disposition,
        },
    )
    return payload


@transaction.atomic
def consume_view_once_attachment(*, actor, attachment_id, request=None):
    attachment = (
        MessageAttachment.objects.select_for_update()
        .select_related("message", "message__conversation", "message__sender")
        .filter(
            id=attachment_id,
            message__conversation__participants__user=actor,
            message__conversation__participants__left_at__isnull=True,
            scan_status=MessageAttachment.ScanStatus.CLEAN,
        )
        .first()
    )
    if not attachment or not attachment.view_once:
        raise ValidationError({"attachment": "View-once attachment was not found."})
    if attachment.message.sender_id == actor.id:
        raise PermissionDenied("Sent view-once media cannot be reopened by the sender.")
    if attachment.media_kind not in {MessageAttachment.MediaKind.IMAGE, MessageAttachment.MediaKind.VIDEO}:
        raise ValidationError({"attachment": "Only images and videos can be viewed once."})
    if MessageAttachmentViewReceipt.objects.filter(attachment=attachment, user=actor).exists():
        raise PermissionDenied("This view-once attachment has already been opened.")
    MessageAttachmentViewReceipt.objects.create(attachment=attachment, user=actor)
    return create_media_access_payload(
        actor=actor,
        resource_type="attachment",
        resource_id=attachment.id,
        request=request,
        disposition="inline",
        purpose="view_once",
    )


@transaction.atomic
def create_direct_conversation(actor, other_user_id):
    other_user_pk = getattr(other_user_id, "id", other_user_id)
    if str(actor.id) == str(other_user_pk):
        raise ValidationError({"participant_ids": "You cannot start a direct conversation with yourself."})
    other_user = other_user_id if getattr(other_user_id, "is_active", None) is not None else None
    if other_user is not None and not other_user.is_active:
        other_user = None
    if other_user is None:
        other_user = User.objects.filter(id=other_user_pk, is_active=True).first()
    if not other_user:
        raise ValidationError({"participant_ids": "User does not exist."})
    if has_block_relationship(actor, other_user):
        raise PermissionDenied("Direct conversation is blocked between these users.")
    direct_key = _direct_key_for_users(actor.id, other_user.id)
    conversation = Conversation.objects.filter(direct_key=direct_key).first()
    if conversation:
        return conversation
    try:
        conversation = Conversation.objects.create(
            type=Conversation.ConversationType.DIRECT,
            created_by=actor,
            direct_key=direct_key,
        )
    except IntegrityError:
        conversation = Conversation.objects.filter(direct_key=direct_key).first()
        if conversation:
            return conversation
        raise
    ConversationParticipant.objects.bulk_create(
        [
            ConversationParticipant(conversation=conversation, user=actor, role=ConversationParticipant.Role.OWNER),
            ConversationParticipant(conversation=conversation, user=other_user, role=ConversationParticipant.Role.MEMBER),
        ]
    )
    return conversation


@transaction.atomic
def create_group_conversation(actor, title, participant_ids, route_name=""):
    title = sanitize_chat_text(title, max_length=255)
    if not title.strip():
        raise ValidationError({"title": "Group title is required."})
    users = list(User.objects.filter(id__in=participant_ids, is_active=True))
    if len(users) != len(set(str(i) for i in participant_ids)):
        raise ValidationError({"participant_ids": "One or more users do not exist."})
    blocked_usernames = []
    for user in users:
        if user.id == actor.id:
            continue
        if has_block_relationship(actor, user):
            blocked_usernames.append(user.username or str(user.id))
    if blocked_usernames:
        raise PermissionDenied(
            f"Group creation is blocked for one or more selected users: {', '.join(blocked_usernames[:5])}."
        )
    requested_route_name = bool(str(route_name or "").strip())
    base_slug = normalize_group_route_name(route_name or title) or f"group-{uuid4().hex[:8]}"
    candidate = base_slug
    suffix = 2
    while Conversation.objects.filter(slug__iexact=candidate).exists() or User.objects.filter(username__iexact=candidate).exists():
        if requested_route_name:
            raise ValidationError({"slug": "This unique group name is already in use."})
        tail = f"-{suffix}"
        candidate = f"{base_slug[:GROUP_ROUTE_NAME_MAX_LENGTH - len(tail)]}{tail}"
        suffix += 1
    conversation = Conversation.objects.create(type=Conversation.ConversationType.GROUP, title=title.strip(), slug=candidate, created_by=actor)
    participants = [ConversationParticipant(conversation=conversation, user=actor, role=ConversationParticipant.Role.OWNER)]
    for user in users:
        if user.id == actor.id:
            continue
        participants.append(ConversationParticipant(conversation=conversation, user=user, role=ConversationParticipant.Role.MEMBER))
    ConversationParticipant.objects.bulk_create(participants)
    return conversation


@transaction.atomic
def add_group_participants(actor, conversation, participant_ids):
    ensure_group_admin(conversation, actor)
    unique_ids = list(dict.fromkeys(str(value) for value in participant_ids))
    users = list(User.objects.filter(id__in=unique_ids, is_active=True))
    if len(users) != len(unique_ids):
        raise ValidationError({"participant_ids": "One or more selected users are unavailable."})
    existing = {str(uid) for uid in conversation.participants.filter(left_at__isnull=True).values_list("user_id", flat=True)}
    blocked_usernames = [user.username or str(user.id) for user in users if has_block_relationship(actor, user)]
    if blocked_usernames:
        raise PermissionDenied(
            f"One or more selected users cannot be added: {', '.join(blocked_usernames[:5])}."
        )
    added_ids = []
    to_create = []
    for user in users:
        if str(user.id) in existing:
            continue
        to_create.append(ConversationParticipant(conversation=conversation, user=user, role=ConversationParticipant.Role.MEMBER))
        added_ids.append(str(user.id))
    if to_create:
        ConversationParticipant.objects.bulk_create(to_create)
        _mark_conversation_e2ee_rekey_required(conversation)
        log_chat_event(ChatAuditLog.EventType.PARTICIPANTS_ADDED, actor=actor, conversation=conversation, metadata={"participant_ids": added_ids})
    return conversation


@transaction.atomic
def remove_group_participant(actor, conversation, user_id):
    actor_participant = ensure_group_admin(conversation, actor)
    participant = ConversationParticipant.objects.filter(conversation=conversation, user_id=user_id, left_at__isnull=True).first()
    if not participant:
        raise ValidationError({"participant": "Participant not found."})
    if participant.role == ConversationParticipant.Role.OWNER:
        raise ValidationError({"participant": "Owner cannot be removed."})
    if participant.role == ConversationParticipant.Role.ADMIN and actor_participant.role != ConversationParticipant.Role.OWNER:
        raise PermissionDenied("Only the owner can remove another admin.")
    participant.left_at = timezone.now()
    participant.save(update_fields=["left_at", "updated_at"])
    _mark_conversation_e2ee_rekey_required(conversation)
    log_chat_event(ChatAuditLog.EventType.PARTICIPANT_REMOVED, actor=actor, conversation=conversation, metadata={"user_id": str(participant.user_id)})
    cleanup_conversation_if_unretained(conversation)
    return participant


@transaction.atomic
def update_participant_role(actor, conversation, target_user_id, role):
    ensure_group_owner(conversation, actor)
    participant = ConversationParticipant.objects.filter(conversation=conversation, user_id=target_user_id, left_at__isnull=True).first()
    if not participant:
        raise ValidationError({"participant": "Participant not found."})
    if participant.role == ConversationParticipant.Role.OWNER:
        raise ValidationError({"participant": "Owner role cannot be changed here."})
    if role not in {ConversationParticipant.Role.MEMBER, ConversationParticipant.Role.ADMIN}:
        raise ValidationError({"role": "Only member/admin roles are assignable here."})
    old_role = participant.role
    participant.role = role
    participant.save(update_fields=["role", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.ROLE_CHANGED, actor=actor, conversation=conversation, metadata={"user_id": str(participant.user_id), "from": old_role, "to": role})
    return participant


@transaction.atomic
def transfer_group_ownership(actor, conversation, target_user_id):
    owner = ensure_group_owner(conversation, actor)
    target = ConversationParticipant.objects.filter(conversation=conversation, user_id=target_user_id, left_at__isnull=True).first()
    if not target:
        raise ValidationError({"participant": "Target participant not found."})
    if target.user_id == actor.id:
        raise ValidationError({"participant": "You already own this conversation."})
    owner.role = ConversationParticipant.Role.ADMIN
    target.role = ConversationParticipant.Role.OWNER
    owner.save(update_fields=["role", "updated_at"])
    target.save(update_fields=["role", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.OWNERSHIP_TRANSFERRED, actor=actor, conversation=conversation, metadata={"from_user_id": str(actor.id), "to_user_id": str(target.user_id)})
    return target


@transaction.atomic
def leave_conversation(actor, conversation):
    participant = ensure_participant(conversation, actor)
    if participant.role == ConversationParticipant.Role.OWNER:
        active_count = conversation.participants.filter(left_at__isnull=True).count()
        if active_count > 1:
            raise ValidationError({"conversation": "Owner cannot leave until ownership is transferred or others are removed."})
    participant.left_at = timezone.now()
    participant.save(update_fields=["left_at", "updated_at"])
    _mark_conversation_e2ee_rekey_required(conversation)
    cleanup_conversation_if_unretained(conversation)
    return participant


def _clone_message_attachments(source_message, target_message):
    for attachment in source_message.attachments.filter(scan_status=MessageAttachment.ScanStatus.CLEAN):
        cloned = MessageAttachment.objects.create(
            message=target_message,
            file=attachment.file,
            original_name=attachment.original_name,
            media_kind=attachment.media_kind,
            mime_type=attachment.mime_type,
            size=attachment.size,
            width=attachment.width,
            height=attachment.height,
            rotation=attachment.rotation,
            duration_seconds=attachment.duration_seconds,
            scan_status=attachment.scan_status,
            scan_notes=attachment.scan_notes,
            scanned_at=attachment.scanned_at,
            metadata=dict(attachment.metadata or {}),
        )
        if attachment.thumbnail:
            with attachment.thumbnail.open("rb") as source:
                cloned.thumbnail.save(Path(attachment.thumbnail.name).name, File(source), save=True)




def _find_existing_message_by_client_temp_id(*, conversation, actor, client_temp_id):
    if not client_temp_id:
        return None
    return (
        Message.objects.select_related("conversation", "sender", "reply_to", "forwarded_from")
        .prefetch_related("attachments", "reactions", "deliveries")
        .filter(conversation=conversation, sender=actor, client_temp_id=client_temp_id)
        .order_by("-created_at")
        .first()
    )


def _lock_conversation_for_send(conversation):
    return Conversation.objects.select_for_update().get(pk=conversation.pk)


def _normalize_message_text(text):
    return " ".join((text or "").strip().lower().split())[:500]


def _enforce_message_abuse_policy(actor, conversation, text):
    now = timezone.now()
    burst_key = f"chat:burst:{actor.id}:{conversation.id}"
    try:
        burst_count = cache.incr(burst_key)
        cache.touch(burst_key, MESSAGE_BURST_WINDOW_SECONDS)
    except ValueError:
        burst_count = 1
        cache.set(burst_key, burst_count, timeout=MESSAGE_BURST_WINDOW_SECONDS)
    if burst_count > MESSAGE_BURST_THRESHOLD:
        raise ValidationError({"text": "Message rate limit exceeded. Please slow down."})

    normalized_text = _normalize_message_text(text)
    if normalized_text:
        link_count = len(re.findall(r"https?://|www\.", normalized_text))
        if link_count > MESSAGE_MAX_LINKS:
            raise ValidationError({"text": "Too many links in one message."})

        duplicate_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        duplicate_key = f"chat:duplicate:{actor.id}:{conversation.id}:{duplicate_hash}"
        try:
            duplicate_count = cache.incr(duplicate_key)
            cache.touch(duplicate_key, MESSAGE_DUPLICATE_WINDOW_SECONDS)
        except ValueError:
            duplicate_count = 1
            cache.set(duplicate_key, duplicate_count, timeout=MESSAGE_DUPLICATE_WINDOW_SECONDS)
        if duplicate_count > MESSAGE_DUPLICATE_THRESHOLD:
            raise ValidationError({"text": "Duplicate messages are temporarily blocked."})


def _validate_uploads_for_message(actor, attachment_ids):
    uploads = list(
        PendingUpload.objects.select_for_update().filter(
            id__in=attachment_ids,
            user=actor,
            status=PendingUpload.UploadStatus.PENDING,
        )
    )
    if len(uploads) != len(set(str(x) for x in attachment_ids)):
        raise ValidationError(
            {"attachment_ids": "One or more uploads are invalid or already attached."}
        )

    for upload in uploads:
        expire_pending_upload_if_needed(upload)
        if upload.status == PendingUpload.UploadStatus.EXPIRED:
            raise ValidationError(
                {"attachment_ids": f"Upload {upload.id} has expired."})
        if upload.scan_status == PendingUpload.ScanStatus.PENDING:
            upload = scan_upload_file(upload)
        if upload.scan_status != PendingUpload.ScanStatus.CLEAN:
            raise ValidationError(
                {"attachment_ids": "Only clean scanned uploads can be attached to a message."}
            )

    return uploads


@transaction.atomic
def send_message(
    actor,
    conversation,
    text="",
    message_type=Message.MessageType.TEXT,
    reply_to_id=None,
    client_temp_id="",
    attachment_ids=None,
    entities=None,
    transcript_payload=None,
    encryption=None,
    attachment_encryption=None,
    view_once_attachment_ids=None,
):
    attachment_ids = attachment_ids or []
    view_once_attachment_ids = {str(value) for value in (view_once_attachment_ids or [])}
    text = sanitize_chat_text(text, max_length=20000, multiline=True)
    client_temp_id = (client_temp_id or "").strip()[:100]
    conversation = _lock_conversation_for_send(conversation)
    ensure_participant_can_send(conversation, actor)
    encryption_payload = _sanitize_encryption_payload(encryption) if encryption else None
    attachment_encryption_payloads = _sanitize_attachment_encryption_payloads(attachment_encryption)

    existing_message = _find_existing_message_by_client_temp_id(
        conversation=conversation,
        actor=actor,
        client_temp_id=client_temp_id,
    )
    if existing_message is not None:
        existing_message._deduplicated_send = True
        return existing_message

    if encryption_payload:
        text = ""
        entities = []
        transcript_payload = None
    if not text and not attachment_ids and not encryption_payload:
        raise ValidationError({"text": "Message text or at least one attachment is required."})
    _enforce_message_abuse_policy(actor, conversation, text)

    reply_to = None
    if reply_to_id:
        reply_to = Message.objects.select_for_update().filter(id=reply_to_id, conversation=conversation).first()
        if not reply_to:
            raise ValidationError({"reply_to_id": "Reply target not found in this conversation."})

    uploads = []
    if attachment_ids:
        uploads = _validate_uploads_for_message(actor, attachment_ids)
        upload_ids = {str(upload.id) for upload in uploads}
        if not view_once_attachment_ids.issubset(upload_ids):
            raise ValidationError({"view_once_attachment_ids": "View-once uploads must belong to this message."})
        invalid_view_once = [
            upload for upload in uploads
            if str(upload.id) in view_once_attachment_ids
            and (upload.media_kind or media_kind_from_mime(upload.mime_type)) not in {PendingUpload.MediaKind.IMAGE, PendingUpload.MediaKind.VIDEO}
        ]
        if invalid_view_once:
            raise ValidationError({"view_once_attachment_ids": "Only images and videos can be sent as view once."})
        requires_encrypted_attachments = bool(encryption_payload or attachment_encryption_payloads)
        missing_encrypted_uploads = [
            str(upload.id) for upload in uploads
            if requires_encrypted_attachments and str(upload.id) not in attachment_encryption_payloads
        ]
        if missing_encrypted_uploads:
            raise ValidationError({"attachment_encryption": "Encrypted messages with attachments must include attachment encryption metadata."})

    if message_type == Message.MessageType.AUDIO and uploads:
        for upload in uploads:
            if not is_voice_like_upload(upload):
                raise ValidationError(
                    {"attachment_ids": "Voice note attachments must be audio files."}
                )

    if encryption_payload:
        _validate_conversation_encryption_payload(
            conversation,
            encryption_payload,
            actor=actor,
            field_name="encryption",
            label="Encrypted message",
        )
    for upload_id, payload in attachment_encryption_payloads.items():
        _validate_conversation_encryption_payload(
            conversation,
            payload,
            actor=actor,
            field_name="attachment_encryption",
            label=f"Encrypted attachment {upload_id}",
        )

    metadata = {}
    if message_type == Message.MessageType.AUDIO:
        metadata = {"voice_note": True}
    if encryption_payload:
        metadata.update({"encrypted": True, "encryption": encryption_payload})
    metadata["raw_text"] = text or ""
    metadata = _prepare_message_metadata(
        conversation=conversation,
        metadata=metadata,
        entities=entities,
        transcript_payload=transcript_payload,
    )

    message = Message.objects.create(
        conversation=conversation,
        sender=actor,
        type=message_type,
        text=text,
        reply_to=reply_to,
        client_temp_id=client_temp_id,
        metadata=metadata,
        delivery_status=Message.DeliveryStatus.SENT,
    )
    if reply_to is not None:
        _lock_message_editing(reply_to, "message_has_replies")
        message._edit_locked_reply_target = reply_to

    attached_any = False
    if uploads:
        for upload in uploads:
            attachment = MessageAttachment(
                message=message,
                original_name=upload.original_name,
                media_kind=upload.media_kind or media_kind_from_mime(upload.mime_type),
                mime_type=upload.mime_type,
                size=upload.size,
                width=upload.width,
                height=upload.height,
                rotation=upload.rotation,
                duration_seconds=upload.duration_seconds,
                scan_status=MessageAttachment.ScanStatus.CLEAN,
                scan_notes=upload.scan_notes,
                scanned_at=upload.scanned_at,
                metadata={
                    **dict(upload.metadata or {}),
                    **(
                        {"encrypted_attachment": True, "encryption": attachment_encryption_payloads[str(upload.id)]}
                        if str(upload.id) in attachment_encryption_payloads
                        else {}
                    ),
                },
                view_once=str(upload.id) in view_once_attachment_ids,
            )
            with upload.file.open("rb") as source:
                attachment.file.save(Path(upload.file.name).name, File(source), save=False)
            if upload.thumbnail:
                with upload.thumbnail.open("rb") as source:
                    attachment.thumbnail.save(Path(upload.thumbnail.name).name, File(source), save=False)
            attachment.save()
            upload.status = PendingUpload.UploadStatus.ATTACHED
            upload.save(update_fields=["status", "updated_at"])
            attached_any = True

    if attached_any and not text and message_type == Message.MessageType.TEXT:
        inferred_type = Message.MessageType.FILE
        if len(uploads) == 1:
            inferred_type = {
                PendingUpload.MediaKind.IMAGE: Message.MessageType.IMAGE,
                PendingUpload.MediaKind.VIDEO: Message.MessageType.VIDEO,
                PendingUpload.MediaKind.AUDIO: Message.MessageType.AUDIO,
            }.get(uploads[0].media_kind or media_kind_from_mime(uploads[0].mime_type), Message.MessageType.FILE)
        message.type = inferred_type
        message.save(update_fields=["type"])

    conversation.last_message = message
    conversation.last_message_at = message.created_at
    conversation.save(update_fields=["last_message", "last_message_at", "updated_at"])
    dispatch_message_notifications(message)
    log_chat_event(
        ChatAuditLog.EventType.MESSAGE_SENT,
        actor=actor,
        conversation=conversation,
        message=message,
        metadata={"attachment_count": len(attachment_ids)},
    )
    if conversation.e2ee_rekey_required and (
        (encryption_payload and int(encryption_payload.get("key_version") or 1) >= int(conversation.e2ee_key_version or 1))
        or any(int(payload.get("key_version") or 1) >= int(conversation.e2ee_key_version or 1) for payload in attachment_encryption_payloads.values())
    ):
        _clear_conversation_rekey_requirement(conversation)
    return message


@transaction.atomic
def forward_message(actor, source_message, target_conversation, client_temp_id=""):
    client_temp_id = (client_temp_id or "").strip()[:100]
    target_conversation = _lock_conversation_for_send(target_conversation)
    ensure_participant(source_message.conversation, actor)
    ensure_participant(target_conversation, actor)
    existing_message = _find_existing_message_by_client_temp_id(conversation=target_conversation, actor=actor, client_temp_id=client_temp_id)
    if existing_message is not None:
        existing_message._deduplicated_send = True
        return existing_message
    source_message = Message.objects.select_for_update().select_related("conversation", "sender").get(pk=source_message.pk)
    if source_message.is_deleted:
        raise ValidationError({"message": "Deleted messages cannot be forwarded."})
    if (source_message.metadata or {}).get("encrypted"):
        raise ValidationError({"message": "Encrypted messages must be decrypted and re-encrypted client-side before forwarding."})
    if source_message.attachments.filter(metadata__encrypted_attachment=True).exists():
        raise ValidationError({"message": "Messages with encrypted attachments must be re-encrypted client-side before forwarding."})
    if source_message.attachments.filter(view_once=True).exists():
        raise ValidationError({"message": "View-once media cannot be forwarded."})
    forwarded = Message.objects.create(
        conversation=target_conversation,
        sender=actor,
        type=source_message.type,
        text=source_message.text,
        metadata=source_message.metadata or {},
        forwarded_from=source_message,
        client_temp_id=client_temp_id,
    )
    _lock_message_editing(source_message, "message_was_forwarded")
    forwarded._edit_locked_source = source_message
    _clone_message_attachments(source_message, forwarded)
    if forwarded.attachments.exists() and not forwarded.text and forwarded.type == Message.MessageType.TEXT:
        forwarded.type = Message.MessageType.FILE
        forwarded.save(update_fields=["type", "updated_at"])
    target_conversation.last_message = forwarded
    target_conversation.last_message_at = forwarded.created_at
    target_conversation.save(update_fields=["last_message", "last_message_at", "updated_at"])
    dispatch_message_notifications(forwarded)
    log_chat_event(ChatAuditLog.EventType.MESSAGE_SENT, actor=actor, conversation=target_conversation, message=forwarded, metadata={"forwarded_from": str(source_message.id)})
    return forwarded


EDIT_LOCK_REASONS = {
    "message_has_reactions": "This message can no longer be edited because someone reacted to it.",
    "message_has_replies": "This message can no longer be edited because it has replies.",
    "message_was_forwarded": "This message can no longer be edited because it was forwarded.",
}


def _lock_message_editing(message, reason):
    if message.edit_locked_at:
        return message
    locked_at = timezone.now()
    updated = Message.objects.filter(pk=message.pk, edit_locked_at__isnull=True).update(
        edit_locked_at=locked_at,
        edit_locked_reason=reason,
    )
    if updated:
        message.edit_locked_at = locked_at
        message.edit_locked_reason = reason
    return message


def get_message_edit_policy(message, actor=None, *, now=None):
    """Return the authoritative edit decision used by both the API and UI."""
    deadline = message.created_at + timedelta(seconds=MESSAGE_EDIT_WINDOW_SECONDS)
    result = {"can_edit": False, "code": "not_editable", "reason": "This message cannot be edited.", "deadline": deadline}
    if actor is None or message.sender_id != getattr(actor, "id", None):
        result.update(code="not_owner", reason="You can edit only your own messages.")
        return result
    if message.is_deleted:
        result.update(code="deleted", reason="Deleted messages cannot be edited.")
        return result
    if message.delivery_status == Message.DeliveryStatus.FAILED:
        result.update(code="failed", reason="Failed messages cannot be edited. Retry or delete the message instead.")
        return result
    editable_types = {Message.MessageType.TEXT, Message.MessageType.IMAGE, Message.MessageType.VIDEO, Message.MessageType.FILE}
    if message.type not in editable_types or (not message.text and not (message.metadata or {}).get("encrypted")):
        result.update(code="unsupported_type", reason="Only text and attachment captions can be edited.")
        return result
    if message.edit_locked_at:
        code = message.edit_locked_reason or "message_activity_locked"
        result.update(code=code, reason=EDIT_LOCK_REASONS.get(code, "This message can no longer be edited because it has activity."))
        return result
    if MESSAGE_EDIT_WINDOW_SECONDS <= 0 or (now or timezone.now()) >= deadline:
        result.update(code="edit_window_expired", reason="The editing window has expired.")
        return result

    result.update(can_edit=True, code="editable", reason="")
    return result


@transaction.atomic
def edit_message(actor, message, text, entities=None, encryption=None):
    # Serialize edits against new activity so stale clients cannot bypass a lock.
    message = Message.objects.select_for_update().select_related("conversation", "sender").get(pk=message.pk)
    policy = get_message_edit_policy(message, actor)
    if not policy["can_edit"]:
        if policy["code"] == "not_owner":
            raise PermissionDenied(policy["reason"])
        raise ValidationError({"detail": policy["reason"], "code": policy["code"]})

    encryption_payload = _sanitize_encryption_payload(encryption) if encryption else None
    existing_metadata = dict(message.metadata or {})
    was_encrypted = bool(existing_metadata.get("encrypted"))

    if encryption_payload:
        _validate_conversation_encryption_payload(
            message.conversation,
            encryption_payload,
            actor=actor,
            field_name="encryption",
            label="Encrypted edit",
        )
        MessageEditHistory.objects.create(
            message=message,
            edited_by=actor,
            previous_text="",
            new_text="",
        )
        existing_metadata.update({
            "encrypted": True,
            "encryption": encryption_payload,
            "raw_text": "",
            "entities": [],
            "links": [],
            "mentioned_user_ids": [],
        })
        message.text = ""
        message.metadata = existing_metadata
    else:
        if was_encrypted:
            raise ValidationError({
                "message": "Encrypted messages must be edited with a new encryption envelope.",
                "code": "e2ee_edit_envelope_required",
            })
        text = sanitize_chat_text(text, max_length=20000, multiline=True)
        previous_text = message.text
        if previous_text == text:
            return message
        MessageEditHistory.objects.create(message=message, edited_by=actor, previous_text=previous_text, new_text=text)
        message.text = text
        existing_metadata["raw_text"] = text or ""
        message.metadata = _prepare_message_metadata(
            conversation=message.conversation,
            metadata=existing_metadata,
            entities=entities,
        )

    message.is_edited = True
    message.edited_at = timezone.now()
    message.save(update_fields=["text", "metadata", "is_edited", "edited_at", "updated_at"])
    if encryption_payload and message.conversation.e2ee_rekey_required:
        _clear_conversation_rekey_requirement(message.conversation)
    log_chat_event(ChatAuditLog.EventType.MESSAGE_EDITED, actor=actor, conversation=message.conversation, message=message)
    return message


@transaction.atomic
def soft_delete_message(actor, message):
    if message.sender_id != actor.id:
        raise PermissionDenied("You can delete only your own messages.")
    if message.is_deleted:
        return message
    message.text = ""
    message.is_deleted = True
    message.deleted_at = timezone.now()
    message.save(update_fields=["text", "is_deleted", "deleted_at", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.MESSAGE_DELETED, actor=actor, conversation=message.conversation, message=message)
    return message


@transaction.atomic
def mark_message_failed(actor, message, reason=""):
    ensure_participant(message.conversation, actor)
    if message.sender_id != actor.id:
        raise PermissionDenied("You can update only your own message state.")
    message.delivery_status = Message.DeliveryStatus.FAILED
    message.failed_reason = (reason or "delivery_failed")[:255]
    message.save(update_fields=["delivery_status", "failed_reason", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.MESSAGE_FAILED, actor=actor, conversation=message.conversation, message=message, metadata={"reason": message.failed_reason})
    return message


@transaction.atomic
def retry_message(actor, message):
    ensure_participant_can_send(message.conversation, actor)
    if message.sender_id != actor.id:
        raise PermissionDenied("You can retry only your own message.")
    if message.delivery_status != Message.DeliveryStatus.FAILED:
        raise ValidationError({"message": "Only failed messages can be retried."})
    message.delivery_status = Message.DeliveryStatus.SENT
    message.failed_reason = ""
    message.retry_count = int(message.retry_count or 0) + 1
    message.save(update_fields=["delivery_status", "failed_reason", "retry_count", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.MESSAGE_RETRIED, actor=actor, conversation=message.conversation, message=message, metadata={"retry_count": message.retry_count})
    return message


@transaction.atomic
def mark_conversation_delivered(actor, conversation, message_id=None):
    participant = ensure_participant(conversation, actor)
    participant._delivery_changed = False
    if message_id:
        if not _is_valid_uuid(message_id):
            return participant
        message = Message.objects.filter(id=message_id, conversation=conversation).first()
        if not message:
            raise ValidationError({"message_id": "Message not found in this conversation."})
    else:
        message = conversation.messages.exclude(sender=actor).order_by("-created_at").first()
    if not message:
        return participant
    if (
        participant.last_delivered_message_id
        and participant.last_delivered_message
        and participant.last_delivered_message.created_at >= message.created_at
    ):
        return participant
    participant.last_delivered_message = message
    participant.last_delivered_at = timezone.now()
    participant.save(update_fields=["last_delivered_message", "last_delivered_at", "updated_at"])
    participant._delivery_changed = True
    undelivered = list(
        Message.objects.filter(conversation=conversation, created_at__lte=message.created_at)
        .exclude(sender=actor)
        .exclude(deliveries__user=actor)
        .values_list("id", flat=True)
    )
    if undelivered:
        MessageDelivery.objects.bulk_create([MessageDelivery(message_id=mid, user=actor) for mid in undelivered], ignore_conflicts=True)
    log_chat_event(ChatAuditLog.EventType.DELIVERY_MARKED, actor=actor, conversation=conversation, message=message)
    return participant


@transaction.atomic
def mark_conversation_read(actor, conversation, message_id=None):
    participant = ensure_participant(conversation, actor)
    participant._read_changed = False
    if message_id:
        if not _is_valid_uuid(message_id):
            return participant
        message = Message.objects.filter(id=message_id, conversation=conversation).first()
        if not message:
            raise ValidationError({"message_id": "Message not found in this conversation."})
    else:
        message = conversation.messages.order_by("-created_at").first()
    if (
        message
        and participant.last_read_message_id
        and participant.last_read_message
        and participant.last_read_message.created_at >= message.created_at
    ):
        return participant
    participant.last_read_message = message
    participant.last_read_at = timezone.now()
    participant.save(update_fields=["last_read_message", "last_read_at", "updated_at"])
    participant._read_changed = bool(message)
    if message:
        mark_conversation_delivered(actor, conversation, message.id)
        log_chat_event(ChatAuditLog.EventType.READ_MARKED, actor=actor, conversation=conversation, message=message)
    return participant


def _is_valid_uuid(value):
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


@transaction.atomic
def add_reaction(actor, message, emoji):
    ensure_participant(message.conversation, actor)
    message = Message.objects.select_for_update().select_related("conversation").get(pk=message.pk)
    MessageReaction.objects.update_or_create(message=message, user=actor, defaults={"emoji": emoji})
    _lock_message_editing(message, "message_has_reactions")
    log_chat_event(ChatAuditLog.EventType.REACTION_ADDED, actor=actor, conversation=message.conversation, message=message, metadata={"emoji": emoji})
    return message


@transaction.atomic
def remove_reaction(actor, message, emoji):
    ensure_participant(message.conversation, actor)
    MessageReaction.objects.filter(message=message, user=actor, emoji=emoji).delete()
    log_chat_event(ChatAuditLog.EventType.REACTION_REMOVED, actor=actor, conversation=message.conversation, message=message, metadata={"emoji": emoji})
    return message


@transaction.atomic
def mute_group_participant(actor, conversation, target_user_id, minutes):
    ensure_group_admin(conversation, actor)
    target = ConversationParticipant.objects.select_for_update().filter(conversation=conversation, user_id=target_user_id, left_at__isnull=True).first()
    if not target:
        raise ValidationError({"user_id": "Participant not found."})
    if target.role == ConversationParticipant.Role.OWNER:
        raise PermissionDenied("Group owner cannot be muted.")
    target.moderation_muted_until = timezone.now() + timedelta(minutes=max(1, int(minutes)))
    target.save(update_fields=["moderation_muted_until", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.PARTICIPANT_MUTED, actor=actor, conversation=conversation, metadata={"target_user_id": str(target.user_id), "minutes": int(minutes)})
    return target


@transaction.atomic
def ban_group_participant(actor, conversation, target_user_id, reason=""):
    actor_participant = ensure_group_admin(conversation, actor)
    target = ConversationParticipant.objects.select_for_update().filter(conversation=conversation, user_id=target_user_id, left_at__isnull=True).first()
    if not target:
        raise ValidationError({"user_id": "Participant not found."})
    if target.role == ConversationParticipant.Role.OWNER:
        raise PermissionDenied("Group owner cannot be banned.")
    if target.role == ConversationParticipant.Role.ADMIN and actor_participant.role != ConversationParticipant.Role.OWNER:
        raise PermissionDenied("Only the owner can prevent another admin from rejoining.")
    target.banned_at = timezone.now()
    target.banned_by = actor
    target.ban_reason = reason[:255]
    target.left_at = timezone.now()
    target.save(update_fields=["banned_at", "banned_by", "ban_reason", "left_at", "updated_at"])
    _mark_conversation_e2ee_rekey_required(conversation)
    log_chat_event(ChatAuditLog.EventType.PARTICIPANT_BANNED, actor=actor, conversation=conversation, metadata={"target_user_id": str(target.user_id), "reason": target.ban_reason})
    return target


@transaction.atomic
def unban_group_participant(actor, conversation, target_user_id):
    ensure_group_admin(conversation, actor)
    target = ConversationParticipant.objects.select_for_update().filter(conversation=conversation, user_id=target_user_id).first()
    if not target:
        raise ValidationError({"user_id": "Participant not found."})
    target.banned_at = None
    target.banned_by = None
    target.ban_reason = ""
    target.left_at = None
    target.save(update_fields=["banned_at", "banned_by", "ban_reason", "left_at", "updated_at"])
    _mark_conversation_e2ee_rekey_required(conversation)
    log_chat_event(ChatAuditLog.EventType.PARTICIPANT_UNBANNED, actor=actor, conversation=conversation, metadata={"target_user_id": str(target.user_id)})
    return target


@transaction.atomic
def block_user(actor, target_user, reason=""):
    if actor.id == target_user.id:
        raise ValidationError({"blocked_user_id": "You cannot block yourself."})
    block, _ = UserBlock.objects.get_or_create(blocker=actor, blocked=target_user, defaults={"reason": reason})
    log_chat_event(ChatAuditLog.EventType.USER_BLOCKED, actor=actor, metadata={"blocked_user_id": str(target_user.id), "reason": reason})
    return block


@transaction.atomic
def unblock_user(actor, target_user):
    UserBlock.objects.filter(blocker=actor, blocked=target_user).delete()
    log_chat_event(ChatAuditLog.EventType.USER_UNBLOCKED, actor=actor, metadata={"blocked_user_id": str(target_user.id)})


@transaction.atomic
def report_message(actor, message, reason, details=""):
    ensure_participant(message.conversation, actor)
    report, _ = MessageReport.objects.get_or_create(message=message, reporter=actor, defaults={"reason": reason, "details": details})
    report_count = MessageReport.objects.filter(message=message).count()
    if report_count >= AUTO_HIDE_REPORT_THRESHOLD and not message.is_deleted:
        message.text = ""
        message.is_deleted = True
        message.deleted_at = timezone.now()
        message.save(update_fields=["text", "is_deleted", "deleted_at", "updated_at"])
        ModerationAction.objects.get_or_create(
            report=report,
            message=message,
            actor=None,
            action_type=ModerationAction.ActionType.HIDE_MESSAGE,
            defaults={"notes": f"Automatically hidden after {report_count} reports."},
        )
        log_chat_event(
            ChatAuditLog.EventType.MODERATION_ACTION,
            actor=None,
            conversation=message.conversation,
            message=message,
            metadata={"action": "auto_hide_message", "report_count": report_count},
        )
    log_chat_event(ChatAuditLog.EventType.REPORT_CREATED, actor=actor, conversation=message.conversation, message=message, metadata={"reason": reason})
    return report


def _ensure_call_access(actor, call):
    ensure_participant(call.conversation, actor)
    if not call.participants.filter(user=actor).exists():
        raise PermissionDenied("You are not part of this call.")


def _reload_call_for_response(call):
    return (
        CallSession.objects.select_related("conversation", "initiated_by", "answered_by")
        .prefetch_related("participants__user")
        .get(id=call.id)
    )


def _format_call_duration_label(duration_seconds):
    total_seconds = max(int(duration_seconds or 0), 0)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _resolve_call_event_payload(call, *, actor=None):
    actor = actor or call.initiated_by
    duration_seconds = 0
    if call.answered_at:
        end_time = call.ended_at or timezone.now()
        duration_seconds = max(int((end_time - call.answered_at).total_seconds()), 0)
    reason = str(call.ended_reason or "").strip().lower()
    if call.status == CallSession.Status.RINGING:
        outcome = "ringing"
        summary_text = "Outgoing call"
    elif call.status == CallSession.Status.ONGOING:
        outcome = "received"
        summary_text = "Call connected"
    elif call.status == CallSession.Status.MISSED:
        outcome = "missed"
        summary_text = "Missed call"
    elif call.status == CallSession.Status.DECLINED or reason == "declined":
        outcome = "declined"
        summary_text = "Call declined"
    elif duration_seconds > 0:
        outcome = "completed"
        summary_text = f"Call ended · {_format_call_duration_label(duration_seconds)}"
    else:
        outcome = "cancelled"
        summary_text = "Call cancelled"
    return {
        "system_event": "call",
        "call_id": str(call.id),
        "call_type": call.call_type,
        "call_status": call.status,
        "call_outcome": outcome,
        "summary_text": summary_text,
        "reason": call.ended_reason or "",
        "duration_seconds": duration_seconds,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "answered_at": call.answered_at.isoformat() if call.answered_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "initiated_by_id": str(call.initiated_by_id) if call.initiated_by_id else None,
        "answered_by_id": str(call.answered_by_id) if call.answered_by_id else None,
        "actor_id": str(actor.id) if actor else None,
    }


def _upsert_call_event_message(call, *, actor=None):
    actor = actor or call.initiated_by
    payload = _resolve_call_event_payload(call, actor=actor)
    target_message = None
    for existing in Message.objects.filter(
        conversation=call.conversation,
        type=Message.MessageType.SYSTEM,
    ).order_by("-created_at")[:25]:
        metadata = existing.metadata or {}
        if metadata.get("system_event") == "call" and str(metadata.get("call_id") or "") == str(call.id):
            target_message = existing
            break
    created = False
    if target_message is None:
        target_message = Message.objects.create(
            conversation=call.conversation,
            sender=actor,
            type=Message.MessageType.SYSTEM,
            text=payload["summary_text"],
            metadata=payload,
        )
        created = True
    else:
        target_message.sender = actor
        target_message.text = payload["summary_text"]
        target_message.metadata = payload
        target_message.is_deleted = False
        target_message.deleted_at = None
        target_message.save(update_fields=["sender", "text", "metadata", "is_deleted", "deleted_at", "updated_at"])
    Conversation.objects.filter(id=call.conversation_id).update(last_message=target_message, last_message_at=target_message.created_at)
    return target_message, created


def _reload_call_with_timeline(call, *, actor=None):
    timeline_message, timeline_created = _upsert_call_event_message(call, actor=actor)
    refreshed = _reload_call_for_response(call)
    refreshed._timeline_message = timeline_message
    refreshed._timeline_message_created = timeline_created
    return refreshed


def start_call(actor, conversation, call_type, metadata=None):
    ensure_participant(conversation, actor)
    if call_type not in {CallSession.CallType.VOICE, CallSession.CallType.VIDEO}:
        raise ValidationError({"call_type": "Unsupported call type."})
    active_call = get_active_call_for_conversation(conversation)
    if active_call:
        _ensure_call_access(actor, active_call)
        return active_call
    actor_active_call = (
        CallSession.objects.filter(
            participants__user=actor,
            participants__state__in=[CallParticipant.State.RINGING, CallParticipant.State.JOINED],
            status__in=ACTIVE_CALL_STATUSES,
        )
        .exclude(conversation=conversation)
        .distinct()
        .order_by("-started_at")
        .first()
    )
    if actor_active_call:
        raise CallParticipantBusy([actor.id], active_call_id=actor_active_call.id, actor_busy=True)
    active_participants = list(ConversationParticipant.objects.filter(conversation=conversation, left_at__isnull=True).select_related("user"))
    callee_user_ids = [participant.user_id for participant in active_participants if participant.user_id != actor.id]
    if not callee_user_ids:
        raise ValidationError({"call": "You need at least one other active participant to start a call."})
    busy_user_ids = list(
        CallParticipant.objects.filter(
            user_id__in=callee_user_ids,
            state__in=[CallParticipant.State.RINGING, CallParticipant.State.JOINED],
            call__status__in=ACTIVE_CALL_STATUSES,
        ).values_list("user_id", flat=True).distinct()
    )
    if busy_user_ids:
        raise CallParticipantBusy(busy_user_ids)
    max_group = get_calling_config()["max_group_call_participants"]
    if conversation.type == Conversation.ConversationType.GROUP and len(active_participants) > max_group:
        raise ValidationError({"call": f"Group calls are limited to {max_group} active participants."})
    with transaction.atomic():
        call = CallSession.objects.create(
            conversation=conversation,
            initiated_by=actor,
            call_type=call_type,
            status=CallSession.Status.RINGING,
            room_key=uuid.uuid4().hex,
            metadata=metadata or {},
        )
        participants = []
        now = timezone.now()
        for participant in active_participants:
            state = CallParticipant.State.JOINED if participant.user_id == actor.id else CallParticipant.State.RINGING
            participants.append(CallParticipant(
                call=call,
                user=participant.user,
                state=state,
                joined_at=now if state == CallParticipant.State.JOINED else None,
            ))
        CallParticipant.objects.bulk_create(participants)
        log_chat_event(ChatAuditLog.EventType.CALL_STARTED, actor=actor, conversation=conversation, metadata={"call_id": str(call.id), "call_type": call.call_type})
        dispatch_call_notifications(call)
    return _reload_call_with_timeline(call, actor=actor)


@transaction.atomic
def accept_call(actor, call):
    _ensure_call_access(actor, call)
    if _expire_call_if_stale(call):
        raise ValidationError({"call": "This call has expired."})
    if call.status not in {CallSession.Status.INITIATED, CallSession.Status.RINGING, CallSession.Status.ONGOING}:
        raise ValidationError({"call": "This call can no longer be accepted."})
    participant = CallParticipant.objects.select_for_update().get(call=call, user=actor)
    now = timezone.now()
    if participant.state != CallParticipant.State.JOINED:
        participant.state = CallParticipant.State.JOINED
        participant.joined_at = participant.joined_at or now
        participant.left_at = None
        participant.save(update_fields=["state", "joined_at", "left_at", "updated_at"])
    if call.status != CallSession.Status.ONGOING:
        call.status = CallSession.Status.ONGOING
        call.answered_by = actor
        call.answered_at = call.answered_at or now
        call.save(update_fields=["status", "answered_by", "answered_at", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.CALL_JOINED, actor=actor, conversation=call.conversation, metadata={"call_id": str(call.id)})
    return _reload_call_with_timeline(call, actor=actor)


@transaction.atomic
def decline_call(actor, call, reason="declined"):
    _ensure_call_access(actor, call)
    if _expire_call_if_stale(call):
        return _reload_call_with_timeline(call, actor=actor)
    if call.status not in {CallSession.Status.INITIATED, CallSession.Status.RINGING, CallSession.Status.ONGOING}:
        raise ValidationError({"call": "This call is already closed."})
    participant = CallParticipant.objects.select_for_update().get(call=call, user=actor)
    now = timezone.now()
    participant.state = CallParticipant.State.DECLINED
    participant.left_at = now
    participant.save(update_fields=["state", "left_at", "updated_at"])
    joined_count = call.participants.filter(state=CallParticipant.State.JOINED).count()
    ringing_count = call.participants.filter(state=CallParticipant.State.RINGING).exclude(user=actor).count()
    if joined_count == 0 and ringing_count == 0:
        call.status = CallSession.Status.DECLINED
        call.ended_at = now
        call.ended_reason = reason
        call.save(update_fields=["status", "ended_at", "ended_reason", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.CALL_DECLINED, actor=actor, conversation=call.conversation, metadata={"call_id": str(call.id), "reason": reason})
    return _reload_call_with_timeline(call, actor=actor)


@transaction.atomic
def end_call(actor, call, reason="ended"):
    _ensure_call_access(actor, call)
    now = timezone.now()
    participant = call.participants.filter(user=actor).first()
    if participant and participant.state != CallParticipant.State.LEFT:
        participant.state = CallParticipant.State.LEFT
        participant.left_at = now
        participant.save(update_fields=["state", "left_at", "updated_at"])
    joined_others = call.participants.filter(state=CallParticipant.State.JOINED).exclude(user=actor).exists()
    should_close_call = call.conversation.type == Conversation.ConversationType.DIRECT or not joined_others or actor_id_is_owner(call, actor)
    if should_close_call:
        call.participants.exclude(user=actor).filter(state__in=[CallParticipant.State.JOINED, CallParticipant.State.RINGING]).update(state=CallParticipant.State.LEFT, left_at=now, updated_at=now)
        call.status = CallSession.Status.ENDED
        call.ended_at = now
        call.ended_reason = reason
        call.save(update_fields=["status", "ended_at", "ended_reason", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.CALL_ENDED, actor=actor, conversation=call.conversation, metadata={"call_id": str(call.id), "reason": reason})
    return _reload_call_with_timeline(call, actor=actor)


@transaction.atomic
def expire_ringing_call(call, reason="missed"):
    if call.status not in {CallSession.Status.INITIATED, CallSession.Status.RINGING}:
        return call
    now = timezone.now()
    call.participants.filter(state=CallParticipant.State.RINGING).update(state=CallParticipant.State.MISSED, left_at=now, updated_at=now)
    call.status = CallSession.Status.MISSED
    call.ended_at = now
    call.ended_reason = reason
    call.save(update_fields=["status", "ended_at", "ended_reason", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.CALL_ENDED, actor=call.initiated_by, conversation=call.conversation, metadata={"call_id": str(call.id), "reason": reason, "auto_expired": True})
    return _reload_call_with_timeline(call, actor=call.initiated_by)


def _expire_call_if_stale(call):
    if call.status not in {CallSession.Status.INITIATED, CallSession.Status.RINGING}:
        return False
    if call.started_at and call.started_at < _ring_timeout_cutoff():
        expire_ringing_call(call)
        call.refresh_from_db()
        return True
    return False


def actor_id_is_owner(call, actor):
    return str(call.initiated_by_id) == str(actor.id)


def send_call_signal(actor, call, signal_type, payload):
    _ensure_call_access(actor, call)
    allowed = {"offer", "answer", "ice_candidate", "renegotiate", "hangup", "busy", "ice_restart", "network_state", "quality_update", "media_toggle", "speaker_hint", "fallback_audio_only", "receiver_report", "request_keyframe"}
    if signal_type not in allowed:
        raise ValidationError({"signal_type": "Unsupported signaling event."})
    payload = dict(payload or {})
    to_user_id = str(payload.get("to_user_id") or "")
    if to_user_id and not call.participants.filter(user_id=to_user_id).exists():
        raise ValidationError({"to_user_id": "Target user is not part of this call."})
    signal_id = _normalize_signal_id(payload.get("signal_id")) or uuid4().hex
    payload["signal_id"] = signal_id
    now = timezone.now()
    if signal_type == "quality_update":
        participant = call.participants.select_for_update().get(user=actor)
        update_fields = ["last_seen_signal_at", "last_quality_report_at", "updated_at"]
        network_quality = payload.get("network_quality")
        if network_quality in CallParticipant.NetworkQuality.values:
            participant.network_quality = network_quality
            update_fields.append("network_quality")
        preferred_video_quality = payload.get("preferred_video_quality")
        if preferred_video_quality in CallParticipant.VideoPreference.values:
            participant.preferred_video_quality = preferred_video_quality
            update_fields.append("preferred_video_quality")
        if "audio_enabled" in payload:
            participant.audio_enabled = bool(payload.get("audio_enabled"))
            update_fields.append("audio_enabled")
        if "video_enabled" in payload:
            participant.video_enabled = bool(payload.get("video_enabled"))
            update_fields.append("video_enabled")
        metrics = payload.get("metrics")
        if isinstance(metrics, dict):
            participant.diagnostics = {**(participant.diagnostics or {}), "quality_signal": metrics}
            update_fields.append("diagnostics")
        participant.last_seen_signal_at = now
        participant.last_quality_report_at = now
        participant.save(update_fields=sorted(set(update_fields)))
        if hasattr(call, "_prefetched_objects_cache"):
            call._prefetched_objects_cache.pop("participants", None)
        payload["recommendation"] = get_call_network_recommendation(call)
    call.last_signal_at = now
    call.save(update_fields=["last_signal_at", "updated_at"])
    metadata = {"call_id": str(call.id), "signal_type": signal_type, "signal_id": signal_id}
    if to_user_id:
        metadata["to_user_id"] = to_user_id
    log_chat_event(ChatAuditLog.EventType.CALL_SIGNAL_SENT, actor=actor, conversation=call.conversation, metadata=metadata)
    signal = {
        "call_id": str(call.id),
        "conversation_id": str(call.conversation_id),
        "signal_id": signal_id,
        "signal_type": signal_type,
        "payload": payload,
        "from_user_id": str(actor.id),
        "sent_at": call.last_signal_at.isoformat(),
    }
    delivered_to = []
    if to_user_id:
        signal["to_user_id"] = to_user_id
        if _append_pending_call_signal(call.id, to_user_id, signal):
            delivered_to.append(to_user_id)
    else:
        recipient_ids = [str(recipient_id) for recipient_id in call.participants.exclude(user=actor).values_list("user_id", flat=True)]
        for recipient_id in recipient_ids:
            if _append_pending_call_signal(call.id, recipient_id, signal):
                delivered_to.append(recipient_id)
    signal["recipient_user_ids"] = delivered_to
    signal["was_deduplicated"] = bool((to_user_id and not delivered_to) or (not to_user_id and len(delivered_to) == 0))
    return signal


@transaction.atomic
def heartbeat_call_participant(actor, call, *, metrics=None, network_quality=None):
    _ensure_call_access(actor, call)
    participant = call.participants.select_for_update().get(user=actor)
    now = timezone.now()
    if network_quality and network_quality in CallParticipant.NetworkQuality.values:
        participant.network_quality = network_quality
    participant.last_heartbeat_at = now
    participant.last_seen_signal_at = now
    participant.reconnecting = False
    participant.reconnect_deadline_at = None
    if metrics:
        participant.diagnostics = metrics
    participant.save(update_fields=["network_quality", "last_heartbeat_at", "last_seen_signal_at", "reconnecting", "reconnect_deadline_at", "diagnostics", "updated_at"])
    call.last_signal_at = now
    call.save(update_fields=["last_signal_at", "updated_at"])
    orchestration = get_call_orchestration(call)
    return {
        "call_id": str(call.id),
        "conversation_id": str(call.conversation_id),
        "user_id": str(actor.id),
        "network_quality": participant.network_quality,
        "last_heartbeat_at": participant.last_heartbeat_at.isoformat(),
        "network_recommendation": get_call_network_recommendation(call),
        "orchestration": orchestration,
        "metrics": participant.diagnostics or {},
    }


@transaction.atomic
def submit_call_quality_report(actor, call, *, packet_loss_pct=None, jitter_ms=None, round_trip_time_ms=None, bitrate_kbps=None, frame_rate=None, network_quality=None, preferred_video_quality=None, audio_enabled=None, video_enabled=None, diagnostics=None):
    _ensure_call_access(actor, call)
    participant = call.participants.select_for_update().get(user=actor)
    now = timezone.now()
    packet_loss_pct = _clamp_float(packet_loss_pct, minimum=0.0, maximum=100.0)
    jitter_ms = _clamp_int(jitter_ms, minimum=0, maximum=10000)
    round_trip_time_ms = _clamp_int(round_trip_time_ms, minimum=0, maximum=60000)
    bitrate_kbps = _clamp_int(bitrate_kbps, minimum=0, maximum=100000)
    frame_rate = _clamp_int(frame_rate, minimum=0, maximum=240)
    if network_quality and network_quality in CallParticipant.NetworkQuality.values:
        participant.network_quality = network_quality
    if preferred_video_quality and preferred_video_quality in CallParticipant.VideoPreference.values:
        participant.preferred_video_quality = preferred_video_quality
    if audio_enabled is not None:
        participant.audio_enabled = bool(audio_enabled)
    if video_enabled is not None:
        participant.video_enabled = bool(video_enabled)
    participant.packet_loss_pct = packet_loss_pct
    participant.jitter_ms = jitter_ms
    participant.round_trip_time_ms = round_trip_time_ms
    participant.bitrate_kbps = bitrate_kbps
    participant.frame_rate = frame_rate
    participant.quality_score = _compute_quality_score(packet_loss_pct=packet_loss_pct, jitter_ms=jitter_ms, round_trip_time_ms=round_trip_time_ms, bitrate_kbps=bitrate_kbps, frame_rate=frame_rate, network_quality=participant.network_quality)
    participant.quality_alert = _quality_alert_from_score(participant.quality_score)
    participant.last_quality_report_at = now
    participant.last_seen_signal_at = now
    participant.last_heartbeat_at = participant.last_heartbeat_at or now
    merged = dict(participant.diagnostics or {})
    if diagnostics:
        merged.update(diagnostics)
    merged["quality_report"] = {
        "packet_loss_pct": packet_loss_pct,
        "jitter_ms": jitter_ms,
        "round_trip_time_ms": round_trip_time_ms,
        "bitrate_kbps": bitrate_kbps,
        "frame_rate": frame_rate,
        "quality_score": participant.quality_score,
        "quality_alert": participant.quality_alert,
        "reported_at": now.isoformat(),
    }
    participant.diagnostics = merged
    participant.save(update_fields=["network_quality", "preferred_video_quality", "audio_enabled", "video_enabled", "packet_loss_pct", "jitter_ms", "round_trip_time_ms", "bitrate_kbps", "frame_rate", "quality_score", "quality_alert", "last_quality_report_at", "last_seen_signal_at", "last_heartbeat_at", "diagnostics", "updated_at"])
    call.last_signal_at = now
    metadata = dict(call.metadata or {})
    metadata["recovery_plan"] = build_call_recovery_plan(call)
    metadata["aggregate_quality"] = get_call_quality_summary(call)
    call.metadata = metadata
    call.save(update_fields=["last_signal_at", "metadata", "updated_at"])
    return {
        "call_id": str(call.id),
        "conversation_id": str(call.conversation_id),
        "user_id": str(actor.id),
        "quality_score": participant.quality_score,
        "quality_alert": participant.quality_alert,
        "network_quality": participant.network_quality,
        "packet_loss_pct": packet_loss_pct,
        "jitter_ms": jitter_ms,
        "round_trip_time_ms": round_trip_time_ms,
        "bitrate_kbps": bitrate_kbps,
        "frame_rate": frame_rate,
        "network_recommendation": get_call_network_recommendation(call),
        "recovery_plan": build_call_recovery_plan(call),
        "aggregate_quality": get_call_quality_summary(call),
        "reported_at": now.isoformat(),
    }


@transaction.atomic
def update_call_media_state(actor, call, *, audio_enabled=None, video_enabled=None, is_on_hold=None, reconnecting=None, screen_share_enabled=None, hand_raised=None, connection_state=None, audio_route=None, preferred_video_quality=None, diagnostics=None):
    _ensure_call_access(actor, call)
    participant = call.participants.select_for_update().get(user=actor)
    now = timezone.now()
    if audio_enabled is not None:
        participant.audio_enabled = bool(audio_enabled)
    if video_enabled is not None:
        participant.video_enabled = bool(video_enabled)
    if is_on_hold is not None:
        participant.is_on_hold = bool(is_on_hold)
    if reconnecting is not None:
        participant.reconnecting = bool(reconnecting)
        participant.reconnect_deadline_at = now + timedelta(seconds=int(getattr(settings, "CALL_RECONNECT_GRACE_SECONDS", 20) or 20)) if reconnecting else None
    if screen_share_enabled is not None:
        participant.screen_share_enabled = bool(screen_share_enabled)
        participant.screen_share_started_at = now if participant.screen_share_enabled else None
    if hand_raised is not None:
        participant.raised_hand_at = now if bool(hand_raised) else None
    if connection_state and connection_state in CallParticipant.ConnectionState.values:
        participant.connection_state = connection_state
    if audio_route and audio_route in CallParticipant.AudioRoute.values:
        participant.audio_route = audio_route
    if preferred_video_quality and preferred_video_quality in CallParticipant.VideoPreference.values:
        participant.preferred_video_quality = preferred_video_quality
    if diagnostics:
        participant.diagnostics = diagnostics
    participant.last_seen_signal_at = now
    participant.last_heartbeat_at = participant.last_heartbeat_at or now
    participant.save(update_fields=["audio_enabled", "video_enabled", "is_on_hold", "reconnecting", "reconnect_deadline_at", "screen_share_enabled", "screen_share_started_at", "raised_hand_at", "connection_state", "audio_route", "preferred_video_quality", "diagnostics", "last_seen_signal_at", "last_heartbeat_at", "updated_at"])
    call.last_signal_at = now
    call.save(update_fields=["last_signal_at", "updated_at"])
    orchestration = get_call_orchestration(call)
    return {
        "call_id": str(call.id),
        "conversation_id": str(call.conversation_id),
        "user_id": str(actor.id),
        "audio_enabled": participant.audio_enabled,
        "video_enabled": participant.video_enabled,
        "is_on_hold": participant.is_on_hold,
        "reconnecting": participant.reconnecting,
        "connection_state": participant.connection_state,
        "audio_route": participant.audio_route,
        "screen_share_enabled": participant.screen_share_enabled,
        "screen_share_started_at": participant.screen_share_started_at.isoformat() if participant.screen_share_started_at else None,
        "hand_raised": bool(participant.raised_hand_at),
        "raised_hand_at": participant.raised_hand_at.isoformat() if participant.raised_hand_at else None,
        "preferred_video_quality": participant.preferred_video_quality,
        "network_recommendation": get_call_network_recommendation(call),
        "orchestration": orchestration,
        "updated_at": now.isoformat(),
    }


@transaction.atomic
def update_call_speaking_state(actor, call, *, speaking_level=0, is_speaking=None):
    _ensure_call_access(actor, call)
    participant = call.participants.select_for_update().get(user=actor)
    now = timezone.now()
    threshold = int(getattr(settings, "CALL_SPEAKER_LEVEL_THRESHOLD", 35) or 35)
    level = max(0, min(int(speaking_level or 0), 100))
    speaking_flag = bool(is_speaking) if is_speaking is not None else level >= threshold
    participant.speaking_level = level
    participant.is_speaking = speaking_flag
    if speaking_flag:
        participant.last_spoke_at = now
    participant.last_seen_signal_at = now
    participant.save(update_fields=["speaking_level", "is_speaking", "last_spoke_at", "last_seen_signal_at", "updated_at"])
    orchestration = get_call_orchestration(call)
    return {
        "call_id": str(call.id),
        "conversation_id": str(call.conversation_id),
        "user_id": str(actor.id),
        "speaking_level": participant.speaking_level,
        "is_speaking": participant.is_speaking,
        "last_spoke_at": participant.last_spoke_at.isoformat() if participant.last_spoke_at else None,
        "orchestration": orchestration,
        "updated_at": now.isoformat(),
    }


def refresh_call_orchestration(call):
    return get_call_orchestration(call)


def expire_stale_call_participants(now=None):
    now = now or timezone.now()
    stale_seconds = int(getattr(settings, "CALL_STALE_PARTICIPANT_SECONDS", 35) or 35)
    cutoff = now - timedelta(seconds=stale_seconds)
    stale_qs = CallParticipant.objects.select_related("call", "call__conversation", "user").filter(
        state=CallParticipant.State.JOINED,
        last_heartbeat_at__isnull=False,
        last_heartbeat_at__lt=cutoff,
        call__status=CallSession.Status.ONGOING,
    )
    affected_calls = set()
    count = 0
    for participant in stale_qs:
        participant.reconnecting = True
        participant.reconnect_deadline_at = now + timedelta(seconds=int(getattr(settings, "CALL_RECONNECT_GRACE_SECONDS", 20) or 20))
        participant.network_quality = CallParticipant.NetworkQuality.OFFLINE
        participant.is_speaking = False
        participant.speaking_level = 0
        participant.save(update_fields=["reconnecting", "reconnect_deadline_at", "network_quality", "is_speaking", "speaking_level", "updated_at"])
        affected_calls.add(participant.call_id)
        count += 1
    drop_qs = CallParticipant.objects.select_related("call", "call__conversation", "user").filter(
        state=CallParticipant.State.JOINED,
        reconnecting=True,
        reconnect_deadline_at__lt=now,
        call__status=CallSession.Status.ONGOING,
    )
    for participant in drop_qs:
        participant.state = CallParticipant.State.LEFT
        participant.left_at = now
        participant.save(update_fields=["state", "left_at", "updated_at"])
        affected_calls.add(participant.call_id)
        count += 1
    for call in CallSession.objects.filter(id__in=affected_calls).prefetch_related("participants"):
        if not call.participants.filter(state=CallParticipant.State.JOINED).exists():
            call.status = CallSession.Status.ENDED
            call.ended_at = now
            call.ended_reason = "network_timeout"
            call.save(update_fields=["status", "ended_at", "ended_reason", "updated_at"])
    return count


def register_device(actor, platform, push_token):
    device, _ = UserDevice.objects.update_or_create(user=actor, push_token=push_token, defaults={"platform": platform, "is_active": True})
    return device


@transaction.atomic
def register_e2ee_device_key(actor, *, device_id, key_id, algorithm, public_key_jwk, label=""):
    device_id = str(device_id or "").strip()[:128]
    key_id = str(key_id or "").strip()[:256]
    algorithm = str(algorithm or "").strip()[:80]
    label = str(label or "").strip()[:120]
    if not device_id:
        raise ValidationError({"device_id": "Device id is required."})
    if not key_id:
        raise ValidationError({"key_id": "Key id is required."})
    if not algorithm:
        raise ValidationError({"algorithm": "Algorithm is required."})
    fingerprint = _fingerprint_public_key_jwk(public_key_jwk)
    existing = UserE2EEDeviceKey.objects.filter(user=actor, key_id=key_id).first()
    if existing and not existing.is_active:
        raise ValidationError({
            "key_id": "This browser encryption key was revoked and cannot be reactivated.",
            "code": "e2ee_device_key_revoked",
        })
    needs_rekey = not existing or existing.fingerprint != fingerprint
    now = timezone.now()
    replaced_count = UserE2EEDeviceKey.objects.filter(user=actor, device_id=device_id, is_active=True).exclude(key_id=key_id).update(
        is_active=False,
        revoked_at=now,
        updated_at=now,
    )
    if replaced_count:
        needs_rekey = True
    key, created = UserE2EEDeviceKey.objects.update_or_create(
        user=actor,
        device_id=device_id,
        key_id=key_id,
        defaults={
            "label": label,
            "algorithm": algorithm,
            "fingerprint": fingerprint,
            "public_key_jwk": public_key_jwk,
            "is_active": True,
            "revoked_at": None,
        },
    )
    security_changed = bool(created or needs_rekey or replaced_count)
    if security_changed:
        _mark_user_conversations_e2ee_rekey_required(actor)
    key._security_changed = security_changed
    return key


def list_e2ee_device_keys_for_actor(actor):
    return UserE2EEDeviceKey.objects.filter(user=actor).order_by("-last_seen_at", "-created_at")


@transaction.atomic
def revoke_e2ee_device_key(actor, *, key_uuid):
    key = UserE2EEDeviceKey.objects.filter(id=key_uuid, user=actor).first()
    if not key:
        raise ValidationError({"key": "E2EE device key was not found."})
    security_changed = bool(key.is_active)
    if security_changed:
        key.is_active = False
        key.revoked_at = timezone.now()
        key.save(update_fields=["is_active", "revoked_at", "updated_at"])
        _mark_user_conversations_e2ee_rekey_required(actor)
    key._security_changed = security_changed
    return key


def get_conversation_e2ee_keys(actor, conversation):
    ensure_participant(conversation, actor)
    participant_ids = list(
        conversation.participants.filter(left_at__isnull=True, banned_at__isnull=True).values_list("user_id", flat=True)
    )
    keys = (
        UserE2EEDeviceKey.objects.filter(user_id__in=participant_ids, is_active=True)
        .select_related("user")
        .order_by("user_id", "-last_seen_at", "-created_at")
    )
    grouped = {}
    for key in keys:
        grouped.setdefault(str(key.user_id), []).append(key)
    return {
        "conversation_id": str(conversation.id),
        "key_version": conversation.e2ee_key_version,
        "rekey_required": conversation.e2ee_rekey_required,
        "last_key_rotation_at": conversation.e2ee_last_key_rotation_at,
        "last_security_event_at": conversation.e2ee_last_security_event_at,
        "participants": grouped,
    }


@transaction.atomic
def deactivate_device(actor, push_token):
    device = UserDevice.objects.filter(user=actor, push_token=push_token).first()
    if not device:
        raise ValidationError({"push_token": "Device token was not found."})
    device.is_active = False
    device.save(update_fields=["is_active", "updated_at"])
    return device


def secure_attachment_queryset_for_user(user):
    return MessageAttachment.objects.filter(
        message__conversation__participants__user=user,
        message__conversation__participants__left_at__isnull=True,
        scan_status=MessageAttachment.ScanStatus.CLEAN,
    ).distinct()


def secure_pending_upload_queryset_for_user(user):
    return PendingUpload.objects.filter(
        user=user,
        scan_status=PendingUpload.ScanStatus.CLEAN,
        status=PendingUpload.UploadStatus.PENDING,
        expires_at__gt=timezone.now(),
    )


@transaction.atomic
def list_message_reports(actor):
    return MessageReport.objects.select_related("message", "reporter", "reporter__profile").order_by("-created_at")


@transaction.atomic
def resolve_message_report(actor, report, notes="", hide_message=False):
    action = ModerationAction.objects.create(report=report, message=report.message, actor=actor, action_type=ModerationAction.ActionType.RESOLVE_REPORT, notes=notes)
    if hide_message and report.message and not report.message.is_deleted:
        report.message.text = ""
        report.message.is_deleted = True
        report.message.deleted_at = timezone.now()
        report.message.save(update_fields=["text", "is_deleted", "deleted_at", "updated_at"])
        ModerationAction.objects.create(report=report, message=report.message, actor=actor, action_type=ModerationAction.ActionType.HIDE_MESSAGE, notes=notes)
    log_chat_event(ChatAuditLog.EventType.MODERATION_ACTION, actor=actor, conversation=getattr(report.message, 'conversation', None), message=report.message, metadata={"action": "resolve_report", "hide_message": hide_message})
    return action


@transaction.atomic
def dismiss_message_report(actor, report, notes=""):
    action = ModerationAction.objects.create(report=report, message=report.message, actor=actor, action_type=ModerationAction.ActionType.DISMISS_REPORT, notes=notes)
    log_chat_event(ChatAuditLog.EventType.MODERATION_ACTION, actor=actor, conversation=getattr(report.message, 'conversation', None), message=report.message, metadata={"action": "dismiss_report"})
    return action


@transaction.atomic
def restore_message_by_staff(actor, message, notes=""):
    message.is_deleted = False
    message.deleted_at = None
    message.save(update_fields=["is_deleted", "deleted_at", "updated_at"])
    action = ModerationAction.objects.create(message=message, actor=actor, action_type=ModerationAction.ActionType.RESTORE_MESSAGE, notes=notes)
    log_chat_event(ChatAuditLog.EventType.MESSAGE_RESTORED, actor=actor, conversation=message.conversation, message=message)
    return action


def use_postgres_search():
    return connection.vendor == "postgresql"



def get_conversation_notification_setting(actor, conversation):
    ensure_participant(conversation, actor)
    setting, _ = ConversationNotificationSetting.objects.get_or_create(conversation=conversation, user=actor)
    return setting


@transaction.atomic
def update_conversation_notification_setting(actor, conversation, **changes):
    setting = get_conversation_notification_setting(actor, conversation)
    dirty = []
    for field in ("message_notifications_enabled", "call_notifications_enabled", "mentions_only", "muted_until"):
        if field in changes:
            setattr(setting, field, changes[field])
            dirty.append(field)
    if dirty:
        setting.save(update_fields=dirty + ["updated_at"])
    return setting


def _generate_invite_token():
    return uuid.uuid4().hex + uuid.uuid4().hex[:8]


@transaction.atomic
def create_conversation_invite_link(actor, conversation, *, expires_in_hours=None, max_uses=0):
    ensure_group_admin(conversation, actor)
    expires_at = None
    if expires_in_hours:
        expires_at = timezone.now() + timedelta(hours=int(expires_in_hours))
    invite = ConversationInviteLink.objects.create(
        conversation=conversation,
        created_by=actor,
        token=_generate_invite_token(),
        expires_at=expires_at,
        max_uses=max(int(max_uses or 0), 0),
    )
    log_chat_event(ChatAuditLog.EventType.PARTICIPANTS_ADDED, actor=actor, conversation=conversation, metadata={"invite_link_id": str(invite.id), "action": "created"})
    return invite


@transaction.atomic
def revoke_conversation_invite_link(actor, invite):
    ensure_group_admin(invite.conversation, actor)
    if not invite.revoked_at:
        invite.revoked_at = timezone.now()
        invite.save(update_fields=["revoked_at", "updated_at"])
    return invite


@transaction.atomic
def join_group_via_invite(actor, token):
    invite = ConversationInviteLink.objects.select_related("conversation").filter(token=token).first()
    if not invite:
        raise ValidationError({"token": "Invite link is invalid."})
    if not invite.is_active:
        raise ValidationError({"token": "Invite link has expired or been revoked."})
    conversation = invite.conversation
    if conversation.type != Conversation.ConversationType.GROUP or not conversation.is_active:
        raise ValidationError({"token": "Invite link is not valid for an active group."})
    participant = ConversationParticipant.objects.filter(conversation=conversation, user=actor).first()
    if participant and participant.left_at is None:
        return conversation
    if participant:
        if participant.banned_at:
            raise PermissionDenied("You are banned from this conversation.")
        participant.left_at = None
        participant.joined_at = timezone.now()
        participant.save(update_fields=["left_at", "joined_at", "updated_at"])
    else:
        ConversationParticipant.objects.create(conversation=conversation, user=actor, role=ConversationParticipant.Role.MEMBER)
    _mark_conversation_e2ee_rekey_required(conversation)
    invite.use_count += 1
    invite.save(update_fields=["use_count", "updated_at"])
    log_chat_event(ChatAuditLog.EventType.PARTICIPANTS_ADDED, actor=actor, conversation=conversation, metadata={"invite_link_id": str(invite.id), "joined_user_id": str(actor.id)})
    return conversation



def list_recent_calls_for_user(user, *, status_filter=None):
    stale_calls = CallSession.objects.filter(
        participants__user=user,
        status__in=[CallSession.Status.INITIATED, CallSession.Status.RINGING],
        started_at__lt=_ring_timeout_cutoff(),
    ).distinct().select_related("conversation", "initiated_by").prefetch_related("participants__user")
    for call in stale_calls:
        expire_ringing_call(call)
    qs = CallSession.objects.filter(participants__user=user).distinct().select_related("conversation", "initiated_by", "answered_by").prefetch_related("participants__user")
    if status_filter:
        qs = qs.filter(status=status_filter)
    return qs.order_by("-started_at")
