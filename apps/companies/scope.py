from contextlib import contextmanager
from contextvars import ContextVar


_current_company = ContextVar(
    "pigs_current_company",
    default=None,
)

_current_membership = ContextVar(
    "pigs_current_membership",
    default=None,
)

_current_permissions = ContextVar(
    "pigs_current_permissions",
    default=frozenset(),
)


def get_current_company():
    return _current_company.get()


def get_current_membership():
    return _current_membership.get()


def get_current_permissions():
    return _current_permissions.get()


def push_company_scope(
    *,
    company,
    membership=None,
    permissions=None,
):
    if (
        permissions is None
        and membership is not None
    ):
        # Local import avoids an import cycle during Django app loading.
        from .authorization import (
            get_effective_permission_codes,
        )

        permissions = get_effective_permission_codes(
            membership
        )

    return (
        _current_company.set(company),
        _current_membership.set(membership),
        _current_permissions.set(
            frozenset(
                permissions or ()
            )
        ),
    )


def pop_company_scope(tokens):
    company_token, membership_token, permissions_token = tokens

    _current_permissions.reset(
        permissions_token
    )
    _current_membership.reset(
        membership_token
    )
    _current_company.reset(
        company_token
    )


@contextmanager
def company_scope(
    company,
    *,
    membership=None,
    permissions=None,
):
    tokens = push_company_scope(
        company=company,
        membership=membership,
        permissions=permissions,
    )

    try:
        yield
    finally:
        pop_company_scope(tokens)