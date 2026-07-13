from pathlib import Path
import os
import sys
import tempfile
from datetime import timedelta
from urllib.parse import urlencode

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
IS_TESTING = "test" in sys.argv


def env_str(name, default=""):
    return os.getenv(name, default)


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() == "true"


def env_int(name, default=0):
    return int(os.getenv(name, str(default)))


def env_csv(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]

USE_LOCAL_TEST_SERVICES = IS_TESTING and env_bool("MESSENGER_TEST_USE_LOCAL_SERVICES", True)

# Core app identity
SECRET_KEY = env_str("SECRET_KEY", "change-me")
DEBUG = env_bool("DEBUG", False)
MESSENGER_ENVIRONMENT = env_str("MESSENGER_ENVIRONMENT", "development" if DEBUG else "production").strip().lower()
MESSENGER_REQUIRE_SECURE_SETTINGS = env_bool("MESSENGER_REQUIRE_SECURE_SETTINGS", MESSENGER_ENVIRONMENT == "production")
NEARBY_LOCATION_TTL_HOURS = max(1, env_int("NEARBY_LOCATION_TTL_HOURS", 24))
APP_DOMAIN = env_str("APP_DOMAIN", "").strip().lower()
ALLOWED_HOSTS = env_csv("ALLOWED_HOSTS", "127.0.0.1,localhost,0.0.0.0,192.168.0.165")
CENTRAL_PROJECT_CODE = env_str("CENTRAL_PROJECT_CODE", "messenger")
CENTRAL_BUSINESS_PRODUCT_CODE = env_str("CENTRAL_BUSINESS_PRODUCT_CODE", "chat")
CENTRAL_AUTH_ENABLED = env_bool("CENTRAL_AUTH_ENABLED", True)
CENTRAL_PAYMENTS_ENABLED = env_bool("CENTRAL_PAYMENTS_ENABLED", True)
CENTRAL_ADMIN_ENABLED = env_bool("CENTRAL_ADMIN_ENABLED", True)
CENTRAL_ACCESS_MODE = env_str(
    "CENTRAL_ACCESS_MODE",
    "enforce" if MESSENGER_REQUIRE_SECURE_SETTINGS else "observe",
).strip().lower()
AUTH_PAYMENT_BASE_URL = env_str("AUTH_PAYMENT_BASE_URL", "http://localhost:8000").rstrip("/")
ADMIN_CONTROL_BASE_URL = env_str("ADMIN_CONTROL_BASE_URL", "http://localhost:8001").rstrip("/")
AUTH_PAYMENT_ADMIN_SERVICE_KEY = env_str("AUTH_PAYMENT_ADMIN_SERVICE_KEY", "")
AUTH_PAYMENT_ADMIN_SIGNING_SECRET = env_str("AUTH_PAYMENT_ADMIN_SIGNING_SECRET", "")
AUTH_PAYMENT_REQUEST_TIMEOUT_SECONDS = env_int("AUTH_PAYMENT_REQUEST_TIMEOUT_SECONDS", 10)
AUTH_PAYMENT_JWT_SIGNING_KEY = env_str(
    "AUTH_PAYMENT_JWT_SIGNING_KEY",
    SECRET_KEY if not CENTRAL_AUTH_ENABLED else "",
)
AUTH_PAYMENT_JWT_PUBLIC_KEY = env_str("AUTH_PAYMENT_JWT_PUBLIC_KEY", "").replace("\\n", "\n")
AUTH_PAYMENT_JWT_ALGORITHM = env_str("AUTH_PAYMENT_JWT_ALGORITHM", "HS256")
CENTRAL_AUTH_PUBLIC_BASE_URL = env_str("CENTRAL_AUTH_PUBLIC_BASE_URL", AUTH_PAYMENT_BASE_URL).rstrip("/")
CENTRAL_ADMIN_PUBLIC_BASE_URL = env_str("CENTRAL_ADMIN_PUBLIC_BASE_URL", ADMIN_CONTROL_BASE_URL).rstrip("/")
CENTRAL_AUTH_CALLBACK_PATH = env_str("CENTRAL_AUTH_CALLBACK_PATH", "/auth/callback/").strip() or "/auth/callback/"
CENTRAL_AUTH_CALLBACK_URL = env_str("CENTRAL_AUTH_CALLBACK_URL", f"{env_str('SITE_URL', 'https://dm.crescentsphere.com').rstrip('/')}{CENTRAL_AUTH_CALLBACK_PATH}").rstrip("/")
CENTRAL_LOGIN_URL = env_str("CENTRAL_LOGIN_URL", f"{CENTRAL_AUTH_PUBLIC_BASE_URL}/login/?{urlencode({'next': CENTRAL_AUTH_CALLBACK_URL})}")
CENTRAL_SIGNUP_URL = env_str("CENTRAL_SIGNUP_URL", f"{CENTRAL_AUTH_PUBLIC_BASE_URL}/signup/?{urlencode({'next': CENTRAL_AUTH_CALLBACK_URL})}")
CENTRAL_ACCOUNT_URL = env_str("CENTRAL_ACCOUNT_URL", f"{CENTRAL_AUTH_PUBLIC_BASE_URL}/account/")
CENTRAL_PASSWORD_RESET_URL = env_str("CENTRAL_PASSWORD_RESET_URL", f"{CENTRAL_AUTH_PUBLIC_BASE_URL}/forgot-password/")
CENTRAL_EMAIL_VERIFICATION_URL = env_str("CENTRAL_EMAIL_VERIFICATION_URL", f"{CENTRAL_AUTH_PUBLIC_BASE_URL}/verify-email/")
CENTRAL_LOGOUT_URL = env_str("CENTRAL_LOGOUT_URL", f"{CENTRAL_AUTH_PUBLIC_BASE_URL}/logout/?{urlencode({'next': env_str('SITE_URL', 'https://dm.crescentsphere.com').rstrip('/') + '/'})}")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "drf_spectacular",
    "django_filters",
    "channels",
    "apps.common",
    "apps.accounts",
    "apps.chat",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "config.middleware.RequestIDMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "config.admin_gate.ProjectAdminGateMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
PROJECT_ADMIN_GATE_SLUG = env_str("PROJECT_ADMIN_GATE_SLUG", "messenger")
PROJECT_ADMIN_GATE_SECRET = env_str("PROJECT_ADMIN_GATE_SECRET", env_str("ADMIN_REAL_GATEWAY_HANDOFF_SECRET", SECRET_KEY))

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend" / "dist"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DB_ENGINE = env_str("DB_ENGINE", "sqlite").lower()
if DB_ENGINE == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env_str("DB_NAME", "snm"),
            "USER": env_str("DB_USER", "postgres"),
            "PASSWORD": env_str("DB_PASSWORD", ""),
            "HOST": env_str("DB_HOST", "127.0.0.1"),
            "PORT": env_str("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / env_str("SQLITE_NAME", "db.sqlite3"),
        }
    }


DATABASE_CONN_MAX_AGE = env_int("DATABASE_CONN_MAX_AGE", 60)
DATABASES["default"]["CONN_MAX_AGE"] = DATABASE_CONN_MAX_AGE

# Security / proxy
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = env_bool("CSRF_COOKIE_HTTPONLY", False)
SECURE_BROWSER_XSS_FILTER = True

APP_VERSION = env_str("APP_VERSION", "v24")
SERVICE_NAME = env_str("SERVICE_NAME", "messenger_api")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"
STATICFILES_DIRS = [FRONTEND_DIST_DIR] if FRONTEND_DIST_DIR.is_dir() else []
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
TEST_TEMP_ROOT = Path(tempfile.gettempdir())
MEDIA_ROOT = TEST_TEMP_ROOT / "messenger-test-media" if USE_LOCAL_TEST_SERVICES else BASE_DIR / "media"
PRIVATE_MEDIA_URL = "/private-media/"
PRIVATE_MEDIA_ROOT = TEST_TEMP_ROOT / "messenger-test-private-media" if USE_LOCAL_TEST_SERVICES else BASE_DIR / "private_media"
PROFILE_AVATAR_MAX_BYTES = env_int("PROFILE_AVATAR_MAX_BYTES", 5 * 1024 * 1024)
PROFILE_AVATAR_MAX_DIMENSION = env_int("PROFILE_AVATAR_MAX_DIMENSION", 1024)
PROFILE_AVATAR_MAX_PIXELS = env_int("PROFILE_AVATAR_MAX_PIXELS", 25_000_000)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

CORS_ALLOWED_ORIGINS = [
    item.strip()
    for item in env_str(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174",
    ).split(",")
    if item.strip()
]
CSRF_TRUSTED_ORIGINS = [
    item.strip()
    for item in env_str(
        "CSRF_TRUSTED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174",
    ).split(",")
    if item.strip()
]
CORS_ALLOW_CREDENTIALS = True


def get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

USE_X_FORWARDED_HOST = get_bool_env("USE_X_FORWARDED_HOST", False)

if get_bool_env("SECURE_PROXY_SSL_HEADER_ENABLED", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", False)

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "config.api.custom_exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "config.authentication.CentralJWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.chat.api.pagination.ChatCursorPagination",
    "PAGE_SIZE": 30,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_THROTTLE_CLASSES": (
        "config.throttling.UnsafeUserRateThrottle",
        "rest_framework.throttling.ScopedRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "user": os.getenv("API_USER_RATE", "200/min"),
        "message_send": env_str("MESSAGE_SEND_RATE", "60/min"),
        "upload_create": env_str("UPLOAD_CREATE_RATE", "20/min"),
        "reaction_write": env_str("REACTION_WRITE_RATE", "120/min"),
        "report_write": env_str("REPORT_WRITE_RATE", "30/min"),
        "block_write": env_str("BLOCK_WRITE_RATE", "30/min"),
        "device_write": env_str("DEVICE_WRITE_RATE", "30/min"),
        "media_token": env_str("MEDIA_TOKEN_RATE", "120/min"),
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "CS Network API",
    "DESCRIPTION": "Backend API documentation for the CS Network Django/DRF service.",
    "VERSION": APP_VERSION,
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "ENUM_NAME_OVERRIDES": {
        "ConversationTypeEnum": [("direct", "Direct"), ("group", "Group")],
        "MessageTypeEnum": [
            ("text", "Text"),
            ("image", "Image"),
            ("video", "Video"),
            ("audio", "Audio"),
            ("file", "File"),
            ("system", "System"),
        ],
        "ConversationParticipantRoleEnum": [("member", "Member"), ("admin", "Admin"), ("owner", "Owner")],
        "CallParticipantStateEnum": [
            ("invited", "Invited"),
            ("ringing", "Ringing"),
            ("joined", "Joined"),
            ("declined", "Declined"),
            ("missed", "Missed"),
            ("left", "Left"),
        ],
        "CallSessionStatusEnum": [
            ("initiated", "Initiated"),
            ("ringing", "Ringing"),
            ("ongoing", "Ongoing"),
            ("declined", "Declined"),
            ("missed", "Missed"),
            ("ended", "Ended"),
            ("failed", "Failed"),
        ],
        "MessageTranscriptStatusEnum": [("pending", "Pending"), ("completed", "Completed"), ("failed", "Failed")],
        "PendingUploadStatusEnum": [
            ("pending", "Pending"),
            ("attached", "Attached"),
            ("rejected", "Rejected"),
            ("expired", "Expired"),
        ],
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_TOKEN_MINUTES", 15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("JWT_REFRESH_TOKEN_DAYS", 7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ALGORITHM": AUTH_PAYMENT_JWT_ALGORITHM,
    "SIGNING_KEY": AUTH_PAYMENT_JWT_SIGNING_KEY,
    "VERIFYING_KEY": AUTH_PAYMENT_JWT_PUBLIC_KEY,
    "ISSUER": env_str("AUTH_PAYMENT_JWT_ISSUER", AUTH_PAYMENT_BASE_URL),
    "AUDIENCE": env_str("AUTH_PAYMENT_JWT_AUDIENCE") or None,
}

REDIS_URL = "" if USE_LOCAL_TEST_SERVICES else env_str("REDIS_URL", "").strip()
if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [REDIS_URL],
            },
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }

cache_location = "" if USE_LOCAL_TEST_SERVICES else env_str("REDIS_CACHE_URL", "")
if cache_location:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": cache_location,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "messenger-api",
        }
    }

MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_BYTES", 15 * 1024 * 1024)
ALLOWED_UPLOAD_EXTENSIONS = [
    item.strip().lower()
    for item in env_str(
        "ALLOWED_UPLOAD_EXTENSIONS",
        "jpg,jpeg,png,gif,webp,pdf,txt,csv,rtf,doc,docx,odt,xls,xlsx,ods,ppt,pptx,odp,mp3,wav,mp4,webm,m4a,aac,3gp,amr,ogg,opus"
    ).split(",")
    if item.strip()
]
ALLOWED_UPLOAD_MIME_TYPES = [
    item.strip().lower()
    for item in env_str(
        "ALLOWED_UPLOAD_MIME_TYPES",
        "image/jpeg,image/png,image/gif,image/webp,application/pdf,text/plain,text/csv,application/csv,application/rtf,text/rtf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.oasis.opendocument.text,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.oasis.opendocument.spreadsheet,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.oasis.opendocument.presentation,application/zip,audio/mpeg,audio/mp3,audio/wav,audio/x-wav,audio/wave,audio/mp4,audio/m4a,audio/x-m4a,audio/aac,audio/3gpp,audio/amr,audio/ogg,audio/opus,audio/webm,video/mp4,video/webm,application/octet-stream"
    ).split(",")
    if item.strip()
]
PRESENCE_TTL_SECONDS = env_int("PRESENCE_TTL_SECONDS", 75)
UPLOAD_SCAN_ASYNC = False if USE_LOCAL_TEST_SERVICES else env_bool("UPLOAD_SCAN_ASYNC", True)

CHAT_ATTACHMENT_FORCE_DOWNLOAD = env_bool("CHAT_ATTACHMENT_FORCE_DOWNLOAD", True)
CELERY_BROKER_URL = "memory://" if USE_LOCAL_TEST_SERVICES else env_str("CELERY_BROKER_URL", REDIS_URL or "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = "cache+memory://" if USE_LOCAL_TEST_SERVICES else env_str("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_ALWAYS_EAGER = True if USE_LOCAL_TEST_SERVICES else env_bool("CELERY_TASK_ALWAYS_EAGER", True)

CHAT_USE_S3_STORAGE = env_bool("CHAT_USE_S3_STORAGE", False)
CLOUDFLARE_R2_ACCOUNT_ID = env_str("CLOUDFLARE_R2_ACCOUNT_ID", "").strip()
CLOUDFLARE_R2_BUCKET_NAME = env_str("CLOUDFLARE_R2_BUCKET_NAME", "").strip()
CLOUDFLARE_R2_ACCESS_KEY_ID = env_str("CLOUDFLARE_R2_ACCESS_KEY_ID", "").strip()
CLOUDFLARE_R2_SECRET_ACCESS_KEY = env_str("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "").strip()
CLOUDFLARE_R2_ENDPOINT_URL = env_str("CLOUDFLARE_R2_ENDPOINT_URL", "").strip()
CLOUDFLARE_R2_CUSTOM_DOMAIN = env_str("CLOUDFLARE_R2_CUSTOM_DOMAIN", "").strip()
R2_STORAGE_CONFIGURED = all(
    [
        CLOUDFLARE_R2_BUCKET_NAME,
        CLOUDFLARE_R2_ACCESS_KEY_ID,
        CLOUDFLARE_R2_SECRET_ACCESS_KEY,
        CLOUDFLARE_R2_ENDPOINT_URL or CLOUDFLARE_R2_ACCOUNT_ID,
    ]
)
CHAT_USE_R2_STORAGE = env_bool("CHAT_USE_R2_STORAGE", False) or R2_STORAGE_CONFIGURED
AWS_ACCESS_KEY_ID = env_str("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = env_str("AWS_SECRET_ACCESS_KEY", "")
AWS_STORAGE_BUCKET_NAME = env_str("AWS_STORAGE_BUCKET_NAME", "")
AWS_S3_REGION_NAME = env_str("AWS_S3_REGION_NAME", "")
AWS_S3_ENDPOINT_URL = env_str("AWS_S3_ENDPOINT_URL", "")
AWS_S3_ADDRESSING_STYLE = env_str("AWS_S3_ADDRESSING_STYLE", "auto")
AWS_S3_CUSTOM_DOMAIN = env_str("AWS_S3_CUSTOM_DOMAIN", "") or None
AWS_S3_FILE_OVERWRITE = env_bool("AWS_S3_FILE_OVERWRITE", False)
AWS_QUERYSTRING_EXPIRE = env_int("AWS_QUERYSTRING_EXPIRE", 300)
AWS_S3_VERIFY = env_bool("AWS_S3_VERIFY", True)

if CHAT_USE_R2_STORAGE:
    CHAT_USE_S3_STORAGE = True
    AWS_ACCESS_KEY_ID = CLOUDFLARE_R2_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = CLOUDFLARE_R2_SECRET_ACCESS_KEY
    AWS_STORAGE_BUCKET_NAME = CLOUDFLARE_R2_BUCKET_NAME
    AWS_S3_ENDPOINT_URL = (
        CLOUDFLARE_R2_ENDPOINT_URL
        or (f"https://{CLOUDFLARE_R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if CLOUDFLARE_R2_ACCOUNT_ID else "")
    )
    AWS_S3_REGION_NAME = "auto"
    # R2 presigned URLs use the S3 API endpoint and path-style bucket URLs.
    # Keep the bucket private; custom domains are intentionally not used for chat files.
    AWS_S3_ADDRESSING_STYLE = env_str("AWS_S3_ADDRESSING_STYLE", "path")
    AWS_S3_CUSTOM_DOMAIN = None
AWS_S3_SIGNATURE_VERSION = env_str("AWS_S3_SIGNATURE_VERSION", "s3v4")
AWS_QUERYSTRING_AUTH = True
AWS_DEFAULT_ACL = None

FIREBASE_SERVICE_ACCOUNT_PATH = env_str("FIREBASE_SERVICE_ACCOUNT_PATH", "")
FIREBASE_PROJECT_ID = env_str("FIREBASE_PROJECT_ID", "")

# Auth / email / social
DEFAULT_FROM_EMAIL = env_str("DEFAULT_FROM_EMAIL", "no-reply@localhost")
EMAIL_BACKEND = env_str("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env_str("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env_str("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env_str("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
FRONTEND_BASE_URL = env_str("FRONTEND_BASE_URL", "http://localhost:5173")
SITE_URL = env_str("SITE_URL", FRONTEND_BASE_URL)
AUTH_REQUIRE_EMAIL_VERIFICATION = env_bool("AUTH_REQUIRE_EMAIL_VERIFICATION", False)
EMAIL_VERIFY_TOKEN_TTL_SECONDS = env_int("EMAIL_VERIFY_TOKEN_TTL_SECONDS", 86400)
PASSWORD_RESET_TOKEN_TTL_SECONDS = env_int("PASSWORD_RESET_TOKEN_TTL_SECONDS", 3600)
OIDC_DISCOVERY_CACHE_TTL_SECONDS = env_int("OIDC_DISCOVERY_CACHE_TTL_SECONDS", 3600)
GOOGLE_OIDC_CLIENT_ID = env_str("GOOGLE_OIDC_CLIENT_ID", "")
APPLE_OIDC_CLIENT_ID = env_str("APPLE_OIDC_CLIENT_ID", "")
AUTH_IP_FAILURE_WINDOW_SECONDS = env_int("AUTH_IP_FAILURE_WINDOW_SECONDS", 900)
AUTH_IP_FAILURE_THRESHOLD = env_int("AUTH_IP_FAILURE_THRESHOLD", 20)
AUTH_IP_BLOCK_TTL_SECONDS = env_int("AUTH_IP_BLOCK_TTL_SECONDS", 1800)

# Messaging / abuse / media
PENDING_UPLOAD_TTL_SECONDS = env_int("PENDING_UPLOAD_TTL_SECONDS", 86400)
MEDIA_TOKEN_TTL_SECONDS = env_int("MEDIA_TOKEN_TTL_SECONDS", 300)
AUTO_HIDE_REPORT_THRESHOLD = env_int("AUTO_HIDE_REPORT_THRESHOLD", 3)
MESSAGE_DUPLICATE_WINDOW_SECONDS = env_int("MESSAGE_DUPLICATE_WINDOW_SECONDS", 120)
MESSAGE_DUPLICATE_THRESHOLD = env_int("MESSAGE_DUPLICATE_THRESHOLD", 3)
MESSAGE_BURST_WINDOW_SECONDS = env_int("MESSAGE_BURST_WINDOW_SECONDS", 30)
MESSAGE_BURST_THRESHOLD = env_int("MESSAGE_BURST_THRESHOLD", 20)
MESSAGE_MAX_LINKS = env_int("MESSAGE_MAX_LINKS", 5)
MESSAGE_MAX_CIPHERTEXT_BYTES = env_int("MESSAGE_MAX_CIPHERTEXT_BYTES", 256 * 1024)
MESSAGE_MAX_ENCRYPTION_ENVELOPE_BYTES = env_int("MESSAGE_MAX_ENCRYPTION_ENVELOPE_BYTES", 300 * 1024)
MEDIA_SERVER_THUMBNAIL_DIMENSION = env_int("MEDIA_SERVER_THUMBNAIL_DIMENSION", 160)
MEDIA_SERVER_THUMBNAIL_JPEG_QUALITY = env_int("MEDIA_SERVER_THUMBNAIL_JPEG_QUALITY", 18)

# Integrations
CLAMAV_ENABLED = env_bool("CLAMAV_ENABLED", False)
CLAMAV_HOST = env_str("CLAMAV_HOST", "127.0.0.1")
CLAMAV_PORT = env_int("CLAMAV_PORT", 3310)
CLAMAV_TIMEOUT_SECONDS = env_int("CLAMAV_TIMEOUT_SECONDS", 10)
CLAMAV_FAIL_OPEN = env_bool("CLAMAV_FAIL_OPEN", False)
FCM_DRY_RUN = env_bool("FCM_DRY_RUN", True)
DEVICE_INACTIVE_DAYS = env_int("DEVICE_INACTIVE_DAYS", 30)

# Calling / signaling
CALL_SIGNAL_QUEUE_TTL_SECONDS = env_int("CALL_SIGNAL_QUEUE_TTL_SECONDS", 180)
CALL_SIGNAL_DEDUP_TTL_SECONDS = env_int("CALL_SIGNAL_DEDUP_TTL_SECONDS", 360)
CALL_OFFER_TIMEOUT_SECONDS = env_int("CALL_OFFER_TIMEOUT_SECONDS", 45)
MAX_GROUP_CALL_PARTICIPANTS = env_int("MAX_GROUP_CALL_PARTICIPANTS", 8)
WEBRTC_ICE_SERVERS_JSON = env_str("WEBRTC_ICE_SERVERS_JSON", "")
WEBRTC_ICE_TRANSPORT_POLICY = env_str("WEBRTC_ICE_TRANSPORT_POLICY", "all")
WEBRTC_ICE_CANDIDATE_POOL_SIZE = env_int("WEBRTC_ICE_CANDIDATE_POOL_SIZE", 4)
WEBRTC_ENABLE_SIMULCAST = env_int("WEBRTC_ENABLE_SIMULCAST", 1)
LOW_BANDWIDTH_VIDEO_MAX_BITRATE_BPS = env_int("LOW_BANDWIDTH_VIDEO_MAX_BITRATE_BPS", 250000)
LOW_BANDWIDTH_VIDEO_MAX_FRAMERATE = env_int("LOW_BANDWIDTH_VIDEO_MAX_FRAMERATE", 12)
LOW_BANDWIDTH_VIDEO_MAX_WIDTH = env_int("LOW_BANDWIDTH_VIDEO_MAX_WIDTH", 320)
LOW_BANDWIDTH_VIDEO_MAX_HEIGHT = env_int("LOW_BANDWIDTH_VIDEO_MAX_HEIGHT", 240)
AUDIO_FALLBACK_MAX_BITRATE_BPS = env_int("AUDIO_FALLBACK_MAX_BITRATE_BPS", 24000)
CALL_AUDIO_ONLY_NETWORK_THRESHOLD = env_str("CALL_AUDIO_ONLY_NETWORK_THRESHOLD", "poor")
CALL_RECONNECT_GRACE_SECONDS = env_int("CALL_RECONNECT_GRACE_SECONDS", 20)
CALL_QUALITY_REPORT_INTERVAL_SECONDS = env_int("CALL_QUALITY_REPORT_INTERVAL_SECONDS", 5)
CALL_HEARTBEAT_INTERVAL_SECONDS = env_int("CALL_HEARTBEAT_INTERVAL_SECONDS", 10)
CALL_STALE_PARTICIPANT_SECONDS = env_int("CALL_STALE_PARTICIPANT_SECONDS", 35)
CALL_ICE_RESTART_BACKOFF_MS = env_int("CALL_ICE_RESTART_BACKOFF_MS", 1500)
CALL_MAX_ICE_RESTARTS = env_int("CALL_MAX_ICE_RESTARTS", 4)
CALL_DOMINANT_SPEAKER_HOLD_MS = env_int("CALL_DOMINANT_SPEAKER_HOLD_MS", 2500)
CALL_SPEAKER_LEVEL_THRESHOLD = env_int("CALL_SPEAKER_LEVEL_THRESHOLD", 35)
CALL_GRID_LAYOUT_THRESHOLD = env_int("CALL_GRID_LAYOUT_THRESHOLD", 4)
CALL_ALLOW_SIMULTANEOUS_SCREEN_SHARES = env_int("CALL_ALLOW_SIMULTANEOUS_SCREEN_SHARES", 0)
TURN_URIS_JSON = env_str("TURN_URIS_JSON", "")
TURN_SHARED_SECRET = env_str("TURN_SHARED_SECRET", "")
TURN_STATIC_USERNAME = env_str("TURN_STATIC_USERNAME", "")
TURN_STATIC_PASSWORD = env_str("TURN_STATIC_PASSWORD", "")
TURN_CREDENTIAL_TTL_SECONDS = env_int("TURN_CREDENTIAL_TTL_SECONDS", 3600)
TURN_REALM = env_str("TURN_REALM", "")
TURN_EXTERNAL_IP = env_str("TURN_EXTERNAL_IP", env_str("DROPLET_PUBLIC_IP", ""))
TURN_RELAY_MIN_PORT = env_int("TURN_RELAY_MIN_PORT", 49160)
TURN_RELAY_MAX_PORT = env_int("TURN_RELAY_MAX_PORT", 49200)

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

REQUEST_LOG_LEVEL = env_str("REQUEST_LOG_LEVEL", "INFO")
DJANGO_LOG_LEVEL = env_str("DJANGO_LOG_LEVEL", "INFO")
CHAT_LOG_LEVEL = env_str("CHAT_LOG_LEVEL", "INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s"
        }
    },
    "filters": {
        "request_id": {
            "()": "config.logging_utils.RequestIDLogFilter",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["request_id"],
        }
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": DJANGO_LOG_LEVEL},
        "apps.chat": {"handlers": ["console"], "level": CHAT_LOG_LEVEL, "propagate": False},
        "django.request": {"handlers": ["console"], "level": REQUEST_LOG_LEVEL, "propagate": False},
    },
}


def _contains_localhost(items: list[str]) -> bool:
    local_markers = ("localhost", "127.0.0.1", "0.0.0.0")
    return any(any(marker in item for marker in local_markers) for item in items)


def _validate_production_settings() -> None:
    if DEBUG:
        raise ImproperlyConfigured("DEBUG must be false in production.")
    if SECRET_KEY == "change-me" or len(SECRET_KEY) < 50:
        raise ImproperlyConfigured("SECRET_KEY must be a strong environment secret in production.")
    if not APP_DOMAIN or "." not in APP_DOMAIN or ":" in APP_DOMAIN or "/" in APP_DOMAIN:
        raise ImproperlyConfigured("APP_DOMAIN must be a public hostname without a scheme or port.")
    if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS or _contains_localhost(ALLOWED_HOSTS):
        raise ImproperlyConfigured("ALLOWED_HOSTS must contain explicit public production hosts only.")
    if APP_DOMAIN not in ALLOWED_HOSTS:
        raise ImproperlyConfigured("ALLOWED_HOSTS must include APP_DOMAIN.")
    expected_origin = f"https://{APP_DOMAIN}"
    if SITE_URL.rstrip("/") != expected_origin or FRONTEND_BASE_URL.rstrip("/") != expected_origin:
        raise ImproperlyConfigured("SITE_URL and FRONTEND_BASE_URL must equal the HTTPS APP_DOMAIN origin.")
    if not CSRF_TRUSTED_ORIGINS or _contains_localhost(CSRF_TRUSTED_ORIGINS):
        raise ImproperlyConfigured("CSRF_TRUSTED_ORIGINS must contain explicit HTTPS production origins.")
    if not all(origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS):
        raise ImproperlyConfigured("CSRF_TRUSTED_ORIGINS must use HTTPS in production.")
    if CORS_ALLOW_CREDENTIALS and (not CORS_ALLOWED_ORIGINS or _contains_localhost(CORS_ALLOWED_ORIGINS)):
        raise ImproperlyConfigured("CORS_ALLOWED_ORIGINS must contain production origins when credentials are enabled.")
    if expected_origin not in CORS_ALLOWED_ORIGINS or expected_origin not in CSRF_TRUSTED_ORIGINS:
        raise ImproperlyConfigured("CORS_ALLOWED_ORIGINS and CSRF_TRUSTED_ORIGINS must include the application origin.")
    if not USE_X_FORWARDED_HOST or not get_bool_env("SECURE_PROXY_SSL_HEADER_ENABLED", False):
        raise ImproperlyConfigured("Trusted reverse-proxy host and HTTPS forwarding must be enabled in production.")
    if DB_ENGINE != "postgres":
        raise ImproperlyConfigured("DB_ENGINE must be postgres in production.")
    if not DB_ENGINE == "postgres" or not env_str("DB_PASSWORD", "").strip():
        raise ImproperlyConfigured("DB_PASSWORD must be set in production.")
    jwt_issuer = str(SIMPLE_JWT.get("ISSUER") or "").strip()
    jwt_audience = str(SIMPLE_JWT.get("AUDIENCE") or "").strip()
    if not jwt_issuer or not jwt_audience:
        raise ImproperlyConfigured("AUTH_PAYMENT_JWT_ISSUER and AUTH_PAYMENT_JWT_AUDIENCE are required in production.")
    if CENTRAL_AUTH_ENABLED and not AUTH_PAYMENT_JWT_PUBLIC_KEY:
        raise ImproperlyConfigured("AUTH_PAYMENT_JWT_PUBLIC_KEY must be set when central auth is enabled.")
    if CENTRAL_AUTH_ENABLED and AUTH_PAYMENT_JWT_ALGORITHM.startswith("HS"):
        raise ImproperlyConfigured("AUTH_PAYMENT_JWT_ALGORITHM must be asymmetric, such as RS256, in production.")
    if not CENTRAL_AUTH_ENABLED and AUTH_PAYMENT_JWT_ALGORITHM.startswith("HS") and len(AUTH_PAYMENT_JWT_SIGNING_KEY) < 64:
        raise ImproperlyConfigured("AUTH_PAYMENT_JWT_SIGNING_KEY must be at least 64 characters for local production auth.")
    if env_int("JWT_ACCESS_TOKEN_MINUTES", 15) > 30:
        raise ImproperlyConfigured("JWT_ACCESS_TOKEN_MINUTES must not exceed 30 in production.")
    if env_int("JWT_REFRESH_TOKEN_DAYS", 7) > 30:
        raise ImproperlyConfigured("JWT_REFRESH_TOKEN_DAYS must not exceed 30 in production.")
    if CENTRAL_ADMIN_ENABLED and not (AUTH_PAYMENT_ADMIN_SERVICE_KEY and AUTH_PAYMENT_ADMIN_SIGNING_SECRET):
        raise ImproperlyConfigured(
            "AUTH_PAYMENT_ADMIN_SERVICE_KEY and AUTH_PAYMENT_ADMIN_SIGNING_SECRET must be set in production."
        )
    if not REDIS_URL:
        raise ImproperlyConfigured("REDIS_URL must be set in production for websocket channel delivery.")
    if not cache_location:
        raise ImproperlyConfigured("REDIS_CACHE_URL must be set in production.")
    if CELERY_TASK_ALWAYS_EAGER:
        raise ImproperlyConfigured("CELERY_TASK_ALWAYS_EAGER must be false in production.")
    if EMAIL_BACKEND.endswith(".console.EmailBackend") or DEFAULT_FROM_EMAIL.endswith("@localhost"):
        raise ImproperlyConfigured("Production email settings must use a real SMTP backend and sender.")
    if EMAIL_BACKEND.endswith(".smtp.EmailBackend") and not (EMAIL_HOST and EMAIL_HOST_USER and EMAIL_HOST_PASSWORD):
        raise ImproperlyConfigured("SMTP host, username, and password are required in production.")
    if not CHAT_USE_R2_STORAGE or not R2_STORAGE_CONFIGURED:
        raise ImproperlyConfigured("Private Cloudflare R2 storage must be fully configured in production.")
    if CLOUDFLARE_R2_CUSTOM_DOMAIN:
        raise ImproperlyConfigured("Private chat R2 storage must not use a public custom domain.")
    if not (TURN_URIS_JSON and TURN_SHARED_SECRET and TURN_REALM and TURN_EXTERNAL_IP):
        raise ImproperlyConfigured("TURN endpoints, secret, realm, and external IP are required in production.")
    if len(TURN_SHARED_SECRET) < 32:
        raise ImproperlyConfigured("TURN_SHARED_SECRET must be at least 32 characters in production.")
    if not AUTH_REQUIRE_EMAIL_VERIFICATION:
        raise ImproperlyConfigured("AUTH_REQUIRE_EMAIL_VERIFICATION must be true in production.")
    if not (SECURE_SSL_REDIRECT and SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE):
        raise ImproperlyConfigured("HTTPS redirect and secure cookies must be enabled in production.")
    if SECURE_HSTS_SECONDS < 31536000:
        raise ImproperlyConfigured("SECURE_HSTS_SECONDS must be at least 31536000 in production.")


if MESSENGER_REQUIRE_SECURE_SETTINGS:
    _validate_production_settings()
