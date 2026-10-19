import uuid
from pathlib import Path
from typing import BinaryIO, Optional

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from src.config import settings

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
    Writes under LOCAL_UPLOAD_DIR, served back out by the /uploads
    StaticFiles mount registered in main.py."""
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
            return None
        return _save_local_fallback(file_obj, key)

    content_type = _SAFE_CONTENT_TYPES.get(extension, _FALLBACK_CONTENT_TYPE)
    extra_args = {
        "ContentType": content_type,
        "ContentDisposition": "inline" if extension in _INLINE_EXTENSIONS else "attachment",
    }
    try:
        client.upload_fileobj(file_obj, settings.R2_BUCKET_NAME, key, ExtraArgs=extra_args)
    except (BotoCoreError, ClientError):
        return None

    if settings.R2_PUBLIC_BASE_URL:
        return f"{settings.R2_PUBLIC_BASE_URL.rstrip('/')}/{key}"

    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.R2_BUCKET_NAME, "Key": key},
            ExpiresIn=604800,
        )
    except (BotoCoreError, ClientError):
        return None
