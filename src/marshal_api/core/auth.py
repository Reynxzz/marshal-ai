"""Role-based access control.

The four dashboard roles are ``supervisor``, ``inspector``, ``worker`` and
``engineer``. The actor's role arrives in the ``X-Actor-Role`` header (a real
token is a later concern); mutative endpoints guard with :class:`RequireRole`.
"""

from __future__ import annotations

from typing import Iterable

from fastapi import Header

from marshal_api.core.errors import ApiError
from marshal_api.schemas.common import Role


def _role_value(role: Role | str) -> str:
    return role.value if isinstance(role, Role) else role


class RequireRole:
    """A dependency that admits only the listed roles to an endpoint."""

    def __init__(self, allowed_roles: Iterable[Role | str]) -> None:
        self.allowed_roles = [_role_value(role) for role in allowed_roles]

    def __call__(
        self,
        x_actor_role: str = Header(default="supervisor", alias="X-Actor-Role"),
    ) -> str:
        if x_actor_role not in self.allowed_roles:
            raise ApiError(
                status_code=403,
                code="forbidden",
                message=(
                    f"Role '{x_actor_role}' unauthorized for this action. "
                    f"Required: {self.allowed_roles}"
                ),
            )
        return x_actor_role
