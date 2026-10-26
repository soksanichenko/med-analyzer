"""On-disk storage of uploaded PDFs: one private directory per user."""

import hashlib
import os
import tempfile
from pathlib import Path

from med_analyzer.log import get_logger

logger = get_logger(__name__)


def save_pdf(storage_dir: Path, user_id: int, data: bytes) -> tuple[str, Path]:
    """Write a PDF to ``<storage_dir>/<user_id>/<sha256>.pdf`` atomically.

    The directories are created with mode 0o700 and the file with mode 0o600.
    The file is written to a temporary name in the same directory and then
    renamed, so a reader never sees a partial PDF.

    Args:
        storage_dir: Root directory of all stored PDFs.
        user_id: Owner of the file.
        data: The PDF bytes.

    Returns:
        The SHA-256 hex digest of ``data`` and the path of the stored file.
    """
    sha256 = hashlib.sha256(data).hexdigest()
    user_dir = storage_dir / str(user_id)
    storage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    user_dir.mkdir(mode=0o700, exist_ok=True)
    path = user_dir / f'{sha256}.pdf'
    # mkstemp creates the file with mode 0o600 regardless of the umask.
    fd, tmp_name = tempfile.mkstemp(dir=user_dir, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    logger.debug('Stored a PDF for user_id=%s, %s bytes', user_id, len(data))
    return sha256, path


def delete_pdf(storage_dir: Path, path: Path) -> None:
    """Remove a stored PDF; a missing file is not an error.

    Args:
        storage_dir: Root directory of all stored PDFs.
        path: The file to remove; must lie inside ``storage_dir``.

    Raises:
        ValueError: If ``path`` resolves to a location outside ``storage_dir``.
    """
    resolved = path.resolve()
    if not resolved.is_relative_to(storage_dir.resolve()):
        raise ValueError('Refusing to delete a file outside the storage directory')
    resolved.unlink(missing_ok=True)
