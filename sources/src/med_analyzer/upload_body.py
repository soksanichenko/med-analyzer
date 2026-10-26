"""Reading an uploaded file from a multipart request within a size limit."""

from collections.abc import AsyncIterator

from fastapi import Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from med_analyzer.config import AppConfig

# Multipart boundaries and headers around the file in the request body.
BODY_OVERHEAD = 64 * 1024


class BodyTooLarge(Exception):
    """The request body went over the size limit while being read."""


class InvalidForm(Exception):
    """The request is not a readable multipart form."""


async def limited_stream(
    stream: AsyncIterator[bytes], limit: int
) -> AsyncIterator[bytes]:
    """Pass the chunks of a body through, failing once it exceeds ``limit``.

    The source is not read any further after the limit is passed, so an
    endless or huge upload is never buffered.

    Args:
        stream: Chunks of the request body.
        limit: Maximum total size in bytes.

    Yields:
        The chunks unchanged.

    Raises:
        BodyTooLarge: More than ``limit`` bytes arrived.
    """
    total = 0
    async for chunk in stream:
        total += len(chunk)
        if total > limit:
            raise BodyTooLarge
        yield chunk


async def read_upload(request: Request, config: AppConfig) -> bytes:
    """Read the ``file`` part of a multipart request within the size limit.

    Args:
        request: The incoming request.
        config: Application settings (size cap).

    Returns:
        The bytes of the uploaded file.

    Raises:
        BodyTooLarge: The body is larger than the cap allows.
        InvalidForm: The body is not multipart or has no file part.
    """
    content_type = request.headers.get('content-type', '')
    if not content_type.lower().startswith('multipart/form-data'):
        raise InvalidForm
    limit = config.max_upload_mb * 1024 * 1024 + BODY_OVERHEAD
    declared = request.headers.get('content-length', '')
    if declared.isascii() and declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge
    parser = MultiPartParser(request.headers, limited_stream(request.stream(), limit))
    try:
        form = await parser.parse()
    except MultiPartException:
        raise InvalidForm from None
    try:
        part = form.get('file')
        if not isinstance(part, UploadFile):
            raise InvalidForm
        return await part.read()
    finally:
        await form.close()
