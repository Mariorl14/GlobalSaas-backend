"""Save / remove business logo files under the uploads directory."""

from __future__ import annotations

import io
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


def save_business_logo(business_id: UUID, file: FileStorage) -> str:
    """
    Persist an uploaded logo and return the public path
    (e.g. ``/uploads/logos/<business_id>/logo.png``).
    """
    if file is None or not getattr(file, "filename", None):
        raise LogoError("Selecciona un archivo de imagen.")

    ext = _ext_of(file.filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise LogoError(
            "Formato no permitido. Usa PNG, JPG, WEBP o GIF.",
            400,
        )

    # Read once to enforce size (FileStorage may not expose content_length).
    data = file.read()
    if not data:
        raise LogoError("El archivo está vacío.")
    if len(data) > MAX_LOGO_BYTES:
        raise LogoError("El logo no puede superar 2 MB.", 400)

    clear_business_logo_files(business_id)
    dest = business_logo_dir(business_id) / f"logo.{ext}"
    dest.write_bytes(data)

    # Cache-bust query so clients refresh after replace.
    token = uuid.uuid4().hex[:8]
    return f"/uploads/logos/{business_id}/logo.{ext}?v={token}"


def delete_business_logo(business_id: UUID) -> None:
    clear_business_logo_files(business_id)


def minimal_png_bytes() -> bytes:
    """1×1 PNG for tests."""
    # Pre-built tiny PNG
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
        b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def file_storage_from_bytes(data: bytes, filename: str = "logo.png") -> FileStorage:
    return FileStorage(stream=io.BytesIO(data), filename=filename, content_type="image/png")
