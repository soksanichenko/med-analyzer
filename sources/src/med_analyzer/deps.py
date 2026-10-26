"""Request-level dependencies: who is calling, and is the request same-origin.

Identity is the ``X-Discord-User-Id`` header and nothing else. It is trusted
because nginx sets it from the ``auth_request`` result (overwriting whatever the
client sent) and the container port is not published, so the app is only
reachable through nginx. There is deliberately no shared secret on top of that.
Never expose the app port directly: anyone who can reach it could pick an
identity.
"""

import re
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from med_analyzer.db import get_db
from med_analyzer.log import get_logger
from med_analyzer.models import User

logger = get_logger(__name__)

USER_ID_HEADER = 'X-Discord-User-Id'
AVATAR_HEADER = 'X-Discord-Avatar-Url'
# A Discord snowflake. ASCII digits only: ``\d`` would also match other scripts.
USER_ID_PATTERN = re.compile(r'[0-9]{1,32}')
# Printable ASCII after the host keeps control characters and spaces out.
AVATAR_PATTERN = re.compile(r'https://cdn\.discordapp\.com/[\x21-\x7e]{0,500}')
SAME_ORIGIN_SITES = ('same-origin', 'none')


class RequestRefused(Exception):
    """A request refused before any handler ran.

    Attributes:
        status_code: HTTP status to answer with.
        message: Plain-text body of the answer.
    """

    def __init__(self, status_code: int, message: str) -> None:
        """Create the refusal.

        Args:
            status_code: HTTP status to answer with.
            message: Plain-text body of the answer.
        """
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Return the user named by the identity header, creating it on first sight.

    Args:
        request: The incoming request.
        db: Session of the request.

    Returns:
        The caller's ``User`` row.

    Raises:
        RequestRefused: The header is missing or is not a Discord id (401).
    """
    discord_id = request.headers.get(USER_ID_HEADER, '')
    # Validated before it gets anywhere near the database.
    if not USER_ID_PATTERN.fullmatch(discord_id):
        raise RequestRefused(401, 'Unauthorized')
    user = db.scalar(select(User).where(User.discord_user_id == discord_id))
    if user is None:
        user = User(discord_user_id=discord_id)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            # A parallel first request created the row between select and insert.
            db.rollback()
            user = db.scalar(select(User).where(User.discord_user_id == discord_id))
        else:
            logger.debug('User created: user_id=%s', user.id)
    logger.debug('Request by user_id=%s discord_user_id=%s', user.id, discord_id)
    return user


def avatar_url(request: Request) -> str | None:
    """Return the Discord avatar URL from the header if it is a CDN link.

    Args:
        request: The incoming request.

    Returns:
        The URL, or ``None`` if absent or not served by the Discord CDN.
    """
    url = request.headers.get(AVATAR_HEADER, '')
    return url if AVATAR_PATTERN.fullmatch(url) else None


def same_origin(request: Request) -> None:
    """Refuse state-changing requests that a browser sent from another site.

    Use it as a dependency of every POST route. ``Sec-Fetch-Site`` decides when
    the browser sends it; otherwise ``Origin`` must match ``Host``. A request
    with neither header (curl, tests) is not a browser cross-site request and
    is allowed.

    Args:
        request: The incoming request.

    Raises:
        RequestRefused: The request comes from another origin (403).
    """
    site = request.headers.get('sec-fetch-site')
    if site is not None:
        if site in SAME_ORIGIN_SITES:
            return
        raise RequestRefused(403, 'Forbidden')
    origin = request.headers.get('origin')
    if origin is None:
        return
    # ``Origin: null`` has an empty netloc and so never equals a real host.
    if urlsplit(origin).netloc != request.headers.get('host'):
        raise RequestRefused(403, 'Forbidden')
