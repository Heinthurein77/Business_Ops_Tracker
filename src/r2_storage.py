import logging
import uuid
from pathlib import Path
from typing import BinaryIO, Optional
from urllib.parse import unquote, urlparse

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException

from src.config import settings

logger = logging.getLogger(__name__)

# D-5: attachment links are bearer URLs, so a stored link must never be
# long-lived. Presigned URLs generated here expire after 15 minutes; the
# session-gated /api/ops/uploads/view endpoint mints a fresh one at view
# time instead of handing out the stored URL.
PRESIGNED_URL_EXPIRY_SECONDS = 900

if settings.IS_PRODUCTION and settings.R2_PUBLIC_BASE_URL:
    # D-5: with a public base URL every upload is effectively public —
    # anyone with the link can read it forever, with no auth. Loud at
    # startup so a deploy can never silently turn private attachments
    # (receipts, report evidence) into public files.
    logger.warning(
        "R2_PUBLIC_BASE_URL is set in production: all uploaded files are "
        "effectively public (no authentication, no expiry). Remove it to "
        "serve attachments only through the session-gated view endpoint."
    )

# Content-Type is derived from the (already extension-allowlisted) filename
# rather than trusted from the caller's UploadFile.content_type, which is
# just whatever the browser/client claims — an attacker can set that to
# anything regardless of the file's real bytes. Passing that straight
# through as the object's stored Content-Type would let a malicious upload
# (e.g. an .png that's actually HTML/script) get served back and rendered
# as that spoofed type by whatever serves the bucket. Any extension not
# listed here (shouldn't happen — every caller pre-validates against its
# own allowlist) falls back to a type that forces a download instead of
# in-browser rendering.
_SAFE_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "txt": "text/plain",
}
_FALLBACK_CONTENT_TYPE = "application/octet-stream"

# Only types whose leading bytes were verified against their extension
# (src/upload_validation.py) and that a browser can safely render are served
# inline; every other stored object (Office documents, csv, txt, anything
# unexpected) is forced to download rather than render in the bucket's origin.
_INLINE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf"}


def is_configured() -> bool:
    return bool(
        settings.R2_ACCOUNT_ID
        and settings.R2_ACCESS_KEY_ID
        and settings.R2_SECRET_ACCESS_KEY
    )


def _get_client():
    if not is_configured():
        return None
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT_URL,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )


def _save_local_fallback(file_obj: BinaryIO, key: str) -> Optional[str]:
    """Dev-only fallback for when R2 isn't configured (see LOCAL_UPLOAD_DIR in
    config.py for why upload_fileobj() never calls this in production).
    Writes under LOCAL_UPLOAD_DIR, served back out by the session-gated
    /uploads route registered in main.py."""
    try:
        dest = Path(settings.LOCAL_UPLOAD_DIR) / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        file_obj.seek(0)
        with open(dest, "wb") as out:
            out.write(file_obj.read())
    except OSError:
        return None
    return f"/uploads/{key}"


def upload_fileobj(
    file_obj: BinaryIO, original_filename: str, folder: str = "activity-attachments"
) -> Optional[str]:
    """Uploads a file-like object (activity attachment or CEO profile photo)
    and returns a retrievable URL.

    Prefers Cloudflare R2 whenever it's configured. When it isn't, falls back
    to local disk storage — but only outside production: Render's filesystem
    is ephemeral, so silently writing there would "work" until the next
    deploy quietly erases every uploaded file. Local dev/Docker has no such
    redeploy churn, so the fallback there is safe and means uploads actually
    work out of the box without needing a Cloudflare account. In production
    with no R2 configured, this still returns None — a loud, visible failure
    callers surface to the user, rather than a silent one that resurfaces
    later as a broken image link.

    @param folder: key prefix so different upload types land in distinct
        "folders" in the bucket (and in the local fallback dir) — defaults to
        the original activity-attachments behavior for existing callers.
    """
    extension = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else "bin"
    key = f"{folder}/{uuid.uuid4().hex}.{extension}"

    client = _get_client()
    if client is None:
        if settings.ENVIRONMENT.lower() == "production":
            logger.error("R2 upload failed: R2 is not configured in production (key=%s)", key)
            return None
        return _save_local_fallback(file_obj, key)

    content_type = _SAFE_CONTENT_TYPES.get(extension, _FALLBACK_CONTENT_TYPE)
    extra_args = {
        "ContentType": content_type,
        "ContentDisposition": "inline" if extension in _INLINE_EXTENSIONS else "attachment",
    }
    try:
        client.upload_fileobj(file_obj, settings.R2_BUCKET_NAME, key, ExtraArgs=extra_args)
    except (BotoCoreError, ClientError) as exc:
        # D-4: every R2 failure is logged with bucket/key/error — a failed
        # upload used to vanish silently (daily-report attachments were
        # dropped with no trace anywhere).
        logger.error("R2 upload failed: bucket=%s key=%s error=%s",
                     settings.R2_BUCKET_NAME, key, exc)
        return None

    if settings.R2_PUBLIC_BASE_URL:
        return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"

    return presigned_url_for_key(key)


def presigned_url_for_key(key: str, expires_in: int = PRESIGNED_URL_EXPIRY_SECONDS) -> Optional[str]:
    """Mint a short-lived presigned GET URL for an already-stored key (D-5:
    generated at view time, never stored). Returns None (and logs) when R2
    is unconfigured or the signing call fails."""
    client = _get_client()
    if client is None:
        logger.error("R2 presign failed: not configured (key=%s)", key)
        return None
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.R2_BUCKET_NAME, "Key": key},
            ExpiresIn=expires_in,
        )
    except (BotoCoreError, ClientError) as exc:
        logger.error("R2 presign failed: bucket=%s key=%s error=%s",
                     settings.R2_BUCKET_NAME, key, exc)
        return None


def extract_r2_key(stored_url: Optional[str]) -> Optional[str]:
    """Recover the storage key from a stored attachment URL (D-5: stored URL
    columns stay as the key reference — no data migration). Handles the
    three shapes ever written: a presigned R2 URL, a public-base URL, and
    the dev-only local fallback (/uploads/<key>). Returns None when the
    value does not look like a stored attachment URL."""
    if not stored_url:
        return None
    parsed = urlparse(stored_url)
    path = unquote(parsed.path or "")
    if path.startswith("/uploads/"):
        return path[len("/uploads/"):]
    # R2 object keys always start with "<folder>/" (see upload_fileobj);
    # the key is the path with any leading slash stripped.
    key = path.lstrip("/")
    if "/" in key and not key.startswith("http"):
        return key
    return None


def upload_attachment_or_503(
    file_obj: BinaryIO, original_filename: str, folder: str = "activity-attachments"
) -> str:
    """D-4: one upload policy for every attachment that is evidence — daily
    reports, expense receipts. When an attachment was provided but the
    upload fails, the request fails loud with 503 instead of saving the
    record as if the evidence were attached. (Profile photos keep their
    existing warn-the-user behaviour and do NOT use this helper.)"""
    url = upload_fileobj(file_obj, original_filename, folder=folder)
    if not url:
        raise HTTPException(
            status_code=503,
            detail="File storage is not available — the attachment could not be saved. Please try again.",
        )
    return url
