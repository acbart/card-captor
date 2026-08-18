"""Image import from folders, ZIP archives and URLs."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import socket
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import AppConfig, get_config
from ..db.models import SourceImage
from . import audit

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}
IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/bmp",
    "image/webp",
    "image/x-ms-bmp",
}
ZIP_MIME_TYPES = {"application/zip", "application/x-zip-compressed", "application/octet-stream"}

#: Magic-number prefixes for supported image formats.
_MAGIC = (
    b"\xff\xd8\xff",          # JPEG
    b"\x89PNG\r\n\x1a\n",     # PNG
    b"II*\x00",               # TIFF LE
    b"MM\x00*",               # TIFF BE
    b"BM",                    # BMP
    b"RIFF",                  # WEBP (RIFF....WEBP)
)

MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


@dataclass
class ImportResult:
    imported: list[SourceImage] = field(default_factory=list)
    skipped_duplicates: int = 0
    rejected: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.imported)


def sanitize_filename(name: str) -> str:
    """Remove path components and dangerous characters from a filename."""
    name = Path(str(name)).name
    name = re.sub(r"[^\w\-_. ]", "_", name)
    name = name.strip(" .") or "image"
    return name[:255]


def validate_import_url(url: str) -> str:
    """Validate a user supplied download URL.

    Only ``http``/``https`` URLs are accepted, embedded credentials are refused
    and hosts that resolve to loopback, link-local, or otherwise private
    addresses are blocked so the importer cannot be pointed at internal
    services. Private hosts can be re-enabled with
    ``CARDCAPTOR_ALLOW_PRIVATE_URL_IMPORT=1`` for a trusted LAN file server.
    """
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http(s) URLs are supported")
    if parsed.userinfo:
        raise ValueError("URLs with embedded credentials are not allowed")
    host = parsed.host
    if not host:
        raise ValueError("URL has no host")
    if os.environ.get("CARDCAPTOR_ALLOW_PRIVATE_URL_IMPORT", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return str(parsed)

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve host: {host}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
        ):
            raise ValueError(
                f"Refusing to download from a private or local address: {address}"
            )
    return str(parsed)


def is_image_file(path: Path) -> bool:
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
    except OSError:
        return False
    return any(header.startswith(prefix) for prefix in _MAGIC)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> list[Path]:
    """Extract image members of a ZIP file, rejecting path traversal attempts."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest_dir.resolve()
    extracted: list[Path] = []

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            member_path = (dest_dir / member).resolve()
            if not member_path.is_relative_to(resolved_dest):
                raise ValueError(f"Unsafe zip path: {member}")
            info = zf.getinfo(member)
            if info.is_dir():
                continue
            if Path(member).suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            zf.extract(member, dest_dir)
            extracted.append(member_path)
    return extracted


def _activity_dir(config: AppConfig, activity_id: int) -> Path:
    directory = config.originals_dir / f"activity_{activity_id}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _store_image(
    session: Session,
    activity_id: int,
    source_path: Path,
    config: AppConfig,
    result: ImportResult,
) -> Optional[SourceImage]:
    if not is_image_file(source_path):
        result.rejected.append(f"{source_path.name}: not a supported image file")
        return None

    digest = sha256_file(source_path)
    existing = session.scalars(
        select(SourceImage).where(
            SourceImage.activity_id == activity_id, SourceImage.sha256 == digest
        )
    ).first()
    if existing is not None:
        result.skipped_duplicates += 1
        return None

    safe_name = sanitize_filename(source_path.name)
    target_dir = _activity_dir(config, activity_id)
    target = target_dir / f"{digest[:12]}_{safe_name}"
    if not target.exists():
        shutil.copy2(source_path, target)

    image = SourceImage(
        activity_id=activity_id,
        original_filename=safe_name,
        stored_path=str(target.relative_to(config.data_dir)),
        sha256=digest,
    )
    session.add(image)
    session.flush()
    audit.log_action(
        session,
        "source_image",
        image.id,
        "import_image",
        "instructor",
        {"activity_id": activity_id, "sha256": digest, "filename": safe_name},
    )
    result.imported.append(image)
    return image


def import_paths(
    session: Session,
    activity_id: int,
    paths: Iterable[Path],
    config: Optional[AppConfig] = None,
) -> ImportResult:
    config = config or get_config()
    config.ensure_dirs()
    result = ImportResult()
    for path in paths:
        path = Path(path)
        if path.is_dir():
            continue
        _store_image(session, activity_id, path, config, result)
    session.commit()
    return result


def allowed_import_roots(config: Optional[AppConfig] = None) -> list[str]:
    """Directories that photographs may be imported from.

    Defaults to the current working directory, the user home directory and the
    application data directory. Extra roots can be added with the
    ``CARDCAPTOR_IMPORT_ROOTS`` environment variable (``os.pathsep`` separated).
    """
    config = config or get_config()
    roots = [os.getcwd(), str(Path.home()), str(config.data_dir)]
    extra = os.environ.get("CARDCAPTOR_IMPORT_ROOTS", "")
    roots.extend(part for part in extra.split(os.pathsep) if part.strip())
    return [os.path.realpath(os.path.expanduser(root)) for root in roots]


def _select_import_root(resolved: str, config: Optional[AppConfig] = None) -> str:
    """Pick the allowed root that contains ``resolved``.

    Returns a root that is *not* a prefix of ``resolved`` when the path is
    outside every allowed directory, so the caller's prefix check fails.
    """
    roots = allowed_import_roots(config)
    for root in roots:
        prefix = root if root.endswith(os.sep) else root + os.sep
        if resolved == root:
            return resolved
        if resolved.startswith(prefix):
            return prefix
    return os.path.join(roots[0], "__not_allowed__") + os.sep


def resolve_import_folder(folder: Path, config: Optional[AppConfig] = None) -> str:
    """Resolve an import folder, refusing anything outside the allowed roots."""
    resolved = os.path.realpath(os.path.expanduser(str(folder)))
    for root in allowed_import_roots(config):
        if resolved == root or resolved.startswith(root + os.sep):
            return resolved
    raise ValueError(
        f"Refusing to import from outside the allowed directories: {folder}"
    )


def import_from_folder(
    session: Session,
    activity_id: int,
    folder: Path,
    config: Optional[AppConfig] = None,
    recursive: bool = True,
) -> ImportResult:
    resolved = os.path.realpath(os.path.expanduser(str(folder)))
    root = _select_import_root(resolved, config)
    if not resolved.startswith(root):
        raise ValueError(
            f"Refusing to import from outside the allowed directories: {folder}"
        )
    if not os.path.isdir(resolved):
        raise NotADirectoryError(f"Not a folder: {resolved}")
    pattern = "**/*" if recursive else "*"
    candidates = sorted(p for p in Path(resolved).glob(pattern) if p.is_file())
    return import_paths(session, activity_id, candidates, config)


def import_from_zip(
    session: Session,
    activity_id: int,
    zip_path: Path,
    config: Optional[AppConfig] = None,
) -> ImportResult:
    config = config or get_config()
    config.ensure_dirs()
    zip_path = Path(zip_path)
    if not zipfile.is_zipfile(zip_path):
        raise ValueError(f"Not a ZIP archive: {zip_path}")

    workdir = config.tmp_dir / sanitize_filename(f"zip_{int(activity_id)}_{zip_path.stem}")
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        extracted = safe_extract_zip(zip_path, workdir)
        return import_paths(session, activity_id, extracted, config)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def import_from_url(
    session: Session,
    activity_id: int,
    url: str,
    config: Optional[AppConfig] = None,
    timeout: float = 60.0,
) -> ImportResult:
    """Download a single image or ZIP archive from a URL and import it."""
    config = config or get_config()
    config.ensure_dirs()

    safe_url = validate_import_url(url)

    # Redirects are followed manually so every hop is validated again and the
    # size limit is enforced while streaming instead of after buffering.
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            with client.stream("GET", safe_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise ValueError("Redirect without a location header")
                    safe_url = validate_import_url(str(response.url.join(location)))
                    continue
                response.raise_for_status()
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                chunks: list[bytes] = []
                downloaded = 0
                for chunk in response.iter_bytes():
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Download exceeds the maximum allowed size")
                    chunks.append(chunk)
                content = b"".join(chunks)
                break
        else:
            raise ValueError("Too many redirects")

    suffix = Path(httpx.URL(safe_url).path).suffix.lower()
    is_zip = content[:4] == b"PK\x03\x04" or suffix == ".zip"
    if not is_zip and content_type and content_type not in IMAGE_MIME_TYPES:
        if suffix not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported content type: {content_type or 'unknown'}")

    filename = sanitize_filename(Path(httpx.URL(safe_url).path).name or "download")
    temp_path = config.tmp_dir / f"download_{activity_id}_{filename}"
    temp_path.write_bytes(content)
    try:
        if is_zip:
            return import_from_zip(session, activity_id, temp_path, config)
        return import_paths(session, activity_id, [temp_path], config)
    finally:
        temp_path.unlink(missing_ok=True)


def import_uploaded_files(
    session: Session,
    activity_id: int,
    files: list[tuple[str, bytes]],
    config: Optional[AppConfig] = None,
) -> ImportResult:
    """Import files uploaded through the web UI (filename, bytes)."""
    config = config or get_config()
    config.ensure_dirs()
    staging = config.tmp_dir / f"upload_{activity_id}"
    staging.mkdir(parents=True, exist_ok=True)
    result = ImportResult()
    try:
        for filename, payload in files:
            safe_name = sanitize_filename(filename)
            suffix = Path(safe_name).suffix.lower()
            staged = staging / safe_name
            staged.write_bytes(payload)
            if suffix == ".zip" or payload[:4] == b"PK\x03\x04":
                sub_result = import_from_zip(session, activity_id, staged, config)
                result.imported.extend(sub_result.imported)
                result.skipped_duplicates += sub_result.skipped_duplicates
                result.rejected.extend(sub_result.rejected)
                continue
            if suffix not in IMAGE_EXTENSIONS:
                result.rejected.append(f"{safe_name}: unsupported file type")
                continue
            _store_image(session, activity_id, staged, config, result)
        session.commit()
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def list_source_images(session: Session, activity_id: int) -> list[SourceImage]:
    return list(
        session.scalars(
            select(SourceImage)
            .where(SourceImage.activity_id == activity_id)
            .order_by(SourceImage.id)
        )
    )
