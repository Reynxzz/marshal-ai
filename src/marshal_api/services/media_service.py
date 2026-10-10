"""Resolve media ids to signed URLs (jobs & media)."""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession


class MediaService:
    """Scaffold: holds the request session; behaviour arrives in a later phase."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
