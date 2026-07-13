import io
import logging
from dataclasses import dataclass, field

from django.conf import settings

logger = logging.getLogger(__name__)

try:
    import clamd
except Exception:  # pragma: no cover
    clamd = None


EICAR_SIGNATURE = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


@dataclass
class AntivirusResult:
    is_clean: bool
    status: str
    notes: str
    engine: str


@dataclass
class AntivirusHealth:
    enabled: bool
    available: bool
    engine: str
    details: str
    ping_ok: bool = False
    version: str = ""
    fail_open: bool = False


def _read_initial_bytes(file_field, limit=1024 * 1024):
    handle = None
    try:
        handle = open(file_field.path, "rb")
        data = handle.read(limit)
    except Exception:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
            handle = None
        try:
            handle = file_field.storage.open(file_field.name, "rb")
            data = handle.read(limit)
        except Exception:
            try:
                file_field.open("rb")
                data = file_field.read(limit)
            finally:
                try:
                    file_field.close()
                except Exception:
                    pass
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
    return data or b""


def _open_file_field_for_scan(file_field):
    try:
        return open(file_field.path, "rb")
    except Exception:
        try:
            return file_field.storage.open(file_field.name, "rb")
        except Exception:
            file_field.open("rb")
            return file_field


def _close_scan_handle(handle, file_field):
    try:
        handle.close()
    except Exception:
        pass
    if handle is file_field:
        try:
            file_field.close()
        except Exception:
            pass


def _get_clamd_client():
    if clamd is None:
        raise RuntimeError("clamd package not installed")
    return clamd.ClamdNetworkSocket(
        host=getattr(settings, "CLAMAV_HOST", "127.0.0.1"),
        port=int(getattr(settings, "CLAMAV_PORT", 3310) or 3310),
        timeout=int(getattr(settings, "CLAMAV_TIMEOUT_SECONDS", 10) or 10),
    )


def antivirus_healthcheck():
    enabled = bool(getattr(settings, "CLAMAV_ENABLED", False))
    fail_open = bool(getattr(settings, "CLAMAV_FAIL_OPEN", False))
    if not enabled:
        return AntivirusHealth(
            enabled=False,
            available=False,
            engine="signature",
            details="ClamAV disabled; signature-only fallback is active.",
            ping_ok=False,
            fail_open=fail_open,
        )
    try:
        client = _get_clamd_client()
        ping_ok = bool(client.ping())
        version = ""
        try:
            version = str(client.version() or "")
        except Exception:
            version = ""
        details = "ClamAV reachable." if ping_ok else "ClamAV ping returned a falsey result."
        return AntivirusHealth(
            enabled=True,
            available=ping_ok,
            engine="clamav",
            details=details,
            ping_ok=ping_ok,
            version=version,
            fail_open=fail_open,
        )
    except Exception as exc:  # pragma: no cover
        return AntivirusHealth(
            enabled=True,
            available=False,
            engine="clamav",
            details=f"ClamAV unavailable: {exc}",
            ping_ok=False,
            fail_open=fail_open,
        )


def _scan_with_clamd(file_field):
    if not getattr(settings, "CLAMAV_ENABLED", False):
        return None
    client = _get_clamd_client()
    handle = _open_file_field_for_scan(file_field)
    try:
        result = client.instream(handle)
    finally:
        _close_scan_handle(handle, file_field)
    _, payload = next(iter(result.items()))
    verdict, details = payload
    verdict = (verdict or "").upper()
    if verdict == "OK":
        return AntivirusResult(True, "clean", "ClamAV scan passed.", "clamav")
    details = details or "Flagged by ClamAV."
    return AntivirusResult(False, "infected", str(details), "clamav")


def scan_file_field(file_field, initial_bytes=None):
    try:
        blob = initial_bytes if initial_bytes is not None else _read_initial_bytes(file_field)
    except Exception as exc:
        logger.warning("Initial antivirus read failed: %s", exc)
        if getattr(settings, "CLAMAV_FAIL_OPEN", False):
            return AntivirusResult(True, "clean", f"Initial read failed; fail-open policy applied: {exc}", "storage-fail-open")
        return AntivirusResult(False, "failed", f"Initial file read failed: {exc}", "storage")
    if EICAR_SIGNATURE in blob:
        return AntivirusResult(False, "infected", "EICAR antivirus test signature detected.", "signature")
    try:
        clam_result = _scan_with_clamd(file_field)
        if clam_result is not None:
            return clam_result
    except Exception as exc:  # pragma: no cover
        logger.warning("ClamAV scan failed: %s", exc)
        if getattr(settings, "CLAMAV_FAIL_OPEN", False):
            return AntivirusResult(True, "clean", f"ClamAV unavailable; fail-open policy applied: {exc}", "clamav-fail-open")
        return AntivirusResult(False, "failed", f"ClamAV scan failed: {exc}", "clamav")
    return AntivirusResult(True, "clean", "Signature scan passed. Configure ClamAV for full antivirus scanning.", "signature")
