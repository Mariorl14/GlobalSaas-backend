"""Save / remove business logo files (local disk or Cloudinary)."""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path
from uuid import UUID

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

ALLOWED_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif"})
MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB


class LogoError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def cloudinary_configured() -> bool:
    if (os.getenv("CLOUDINARY_URL") or "").strip():
        return True
    return all(
        (os.getenv(name) or "").strip()
        for name in (
            "CLOUDINARY_CLOUD_NAME",
            "CLOUDINARY_API_KEY",
            "CLOUDINARY_API_SECRET",
        )
    )


def _init_cloudinary() -> None:
    import cloudinary

    url = (os.getenv("CLOUDINARY_URL") or "").strip()
    if url:
        cloudinary.config(cloudinary_url=url, secure=True)
        return
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def logo_public_id(business_id: UUID) -> str:
    return f"barber-logos/{business_id}/logo"


def uploads_root() -> Path:
    configured = current_app.config.get("UPLOAD_FOLDER")
    if configured:
        root = Path(configured)
    else:
        root = Path(current_app.root_path).resolve().parent / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def business_logo_dir(business_id: UUID) -> Path:
    path = uploads_root() / "logos" / str(business_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ext_of(filename: str) -> str:
    name = secure_filename(filename or "")
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def clear_business_logo_files(business_id: UUID) -> None:
    folder = business_logo_dir(business_id)
    if not folder.exists():
        return
    for child in folder.iterdir():
        if child.is_file():
            try:
                child.unlink()
            except OSError:
                pass


def _delete_cloudinary_logo(business_id: UUID) -> None:
    if not cloudinary_configured():
        return
    _init_cloudinary()
    import cloudinary.uploader

    try:
        cloudinary.uploader.destroy(logo_public_id(business_id), invalidate=True)
    except Exception:
        pass


def _save_cloudinary_logo(business_id: UUID, data: bytes, ext: str) -> str:
    _init_cloudinary()
    import cloudinary.uploader

    clear_business_logo_files(business_id)
    _delete_cloudinary_logo(business_id)

    result = cloudinary.uploader.upload(
        data,
        public_id=logo_public_id(business_id),
        overwrite=True,
        resource_type="image",
        format=ext,
    )
    token = uuid.uuid4().hex[:8]
    return f"{result['secure_url']}?v={token}"


def save_business_logo(business_id: UUID, file: FileStorage) -> str:
    """
    Persist an uploaded logo and return a public URL or path.

    Uses Cloudinary when ``CLOUDINARY_URL`` (or cloud name/key/secret) is set;
    otherwise writes under ``UPLOAD_FOLDER`` (ephemeral on Render unless mounted).
    """
    if file is None or not getattr(file, "filename", None):
        raise LogoError("Selecciona un archivo de imagen.")

    ext = _ext_of(file.filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise LogoError(
            "Formato no permitido. Usa PNG, JPG, WEBP o GIF.",
            400,
        )

    data = file.read()
    if not data:
        raise LogoError("El archivo está vacío.")
    if len(data) > MAX_LOGO_BYTES:
        raise LogoError("El logo no puede superar 2 MB.", 400)

    if cloudinary_configured():
        return _save_cloudinary_logo(business_id, data, ext)

    clear_business_logo_files(business_id)
    dest = business_logo_dir(business_id) / f"logo.{ext}"
    dest.write_bytes(data)

    token = uuid.uuid4().hex[:8]
    return f"/uploads/logos/{business_id}/logo.{ext}?v={token}"


def delete_business_logo(business_id: UUID) -> None:
    _delete_cloudinary_logo(business_id)
    clear_business_logo_files(business_id)


def minimal_png_bytes() -> bytes:
    """1×1 PNG for tests."""
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
        b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def file_storage_from_bytes(data: bytes, filename: str = "logo.png") -> FileStorage:
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type="image/png")
