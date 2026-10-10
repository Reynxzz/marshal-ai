"""Human-authority proposal execution (Flows 1, 3, 5)."""

from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession


class ProposalService:
    """Scaffold: holds the request session; behaviour arrives in a later phase."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
