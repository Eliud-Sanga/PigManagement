from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class CentralIdentity:
    subject: UUID
    username: str
    email: str
    is_platform_admin: bool = False


class CentralAuthAdapter(Protocol):
    """
    Future CentralAuth boundary.

    CentralAuth establishes/synchronizes global identity and system
    authority. Daily company authorization remains local to PIGS.
    """

    def exchange_login_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> CentralIdentity:
        ...

    def refresh_identity(
        self,
        *,
        subject: UUID,
    ) -> CentralIdentity:
        ...


class CentralAuthDisabled:
    def exchange_login_code(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> CentralIdentity:
        raise RuntimeError(
            "CentralAuth integration is currently disabled."
        )

    def refresh_identity(
        self,
        *,
        subject: UUID,
    ) -> CentralIdentity:
        raise RuntimeError(
            "CentralAuth integration is currently disabled."
        )
