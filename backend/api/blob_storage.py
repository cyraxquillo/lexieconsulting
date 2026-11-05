import os
import re
import unicodedata
from pathlib import PurePath
from uuid import uuid4


MAX_SUBMISSION_FILE_SIZE = 4_000_000
ALLOWED_SUBMISSION_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
}


def sanitize_filename(filename):
    normalized = unicodedata.normalize("NFKC", PurePath(filename).name)
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", normalized).strip("._")
    return sanitized[:200] or "submission"


def upload_submission_blob(employee_id, uploaded_file):
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured.")
    from vercel.blob import sync

    filename = sanitize_filename(uploaded_file.name)
    uploaded_file.seek(0)
    result = sync.put_blob(
        f"submissions/{employee_id}/{uuid4().hex}-{filename}",
        uploaded_file.read(),
        access="private",
        content_type=uploaded_file.content_type or "application/octet-stream",
    )
    return result.pathname


def upload_profile_photo_blob(user_id, uploaded_file):
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured.")
    from vercel.blob import sync

    extension = PurePath(uploaded_file.name).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png"}:
        raise ValueError("Only JPG and PNG profile photos are supported.")
    uploaded_file.seek(0)
    result = sync.put_blob(
        f"profiles/{user_id}/{uuid4().hex}{extension}",
        uploaded_file.read(),
        access="private",
        content_type=uploaded_file.content_type or "image/jpeg",
    )
    return result.pathname


def get_submission_blob(pathname):
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured.")
    from vercel.blob import sync

    return sync.get_blob(pathname, access="private")


def delete_profile_photo_blob(pathname):
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured.")
    from vercel.blob import sync

    sync.delete_blob(pathname)
