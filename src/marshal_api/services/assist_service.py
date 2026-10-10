"""Remote assistance decisions and escalation (Flow 2)."""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession


class AssistanceService:
    """Scaffold: holds the request session; behaviour arrives in a later phase."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
