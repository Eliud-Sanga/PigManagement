import json
import os
import urllib.error
import urllib.parse
import urllib.request


CENTRALAUTH_PERMISSION_URL = os.environ.get(
    "CENTRALAUTH_PERMISSION_URL",
    "http://127.0.0.1:8100/accounts/sso/permissions/",
)

CENTRALAUTH_PERMISSION_OVERRIDE_URL = os.environ.get(
    "CENTRALAUTH_PERMISSION_OVERRIDE_URL",
    "http://127.0.0.1:8100/accounts/sso/permission-overrides/",
)

CENTRALAUTH_CLIENT_ID = os.environ.get(
    "CENTRALAUTH_CLIENT_ID",
)

CENTRALAUTH_CLIENT_SECRET = os.environ.get(
    "CENTRALAUTH_CLIENT_SECRET",
)


class CentralAuthPermissionError(Exception):
    """Raised when CentralAuth permission resolution fails."""


def get_central_permissions(central_subject):
    """
    Fetch effective permissions for a PIGS user from CentralAuth.

    Returns:
        set[str]
    """

    if not central_subject:
        return set()

    if not CENTRALAUTH_CLIENT_ID:
        raise CentralAuthPermissionError(
            "CENTRALAUTH_CLIENT_ID is not configured."
        )

    if not CENTRALAUTH_CLIENT_SECRET:
        raise CentralAuthPermissionError(
            "CENTRALAUTH_CLIENT_SECRET is not configured."
        )

    payload = urllib.parse.urlencode(
        {
            "client_id": CENTRALAUTH_CLIENT_ID,
            "client_secret": CENTRALAUTH_CLIENT_SECRET,
            "sub": str(central_subject),
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        CENTRALAUTH_PERMISSION_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": (
                "application/x-www-form-urlencoded"
            ),
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=5,
        ) as response:

            response_data = json.loads(
                response.read().decode("utf-8")
            )

    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:

        raise CentralAuthPermissionError(
            "Could not resolve permissions from CentralAuth."
        ) from exc

    if response.status != 200:
        raise CentralAuthPermissionError(
            (
                "CentralAuth permission request failed "
                f"with HTTP {response.status}."
            )
        )

    permissions = response_data.get(
        "permissions",
        [],
    )

    if not isinstance(permissions, list):
        raise CentralAuthPermissionError(
            "CentralAuth returned an invalid permissions payload."
        )

    return {
        str(permission).strip().lower()
        for permission in permissions
        if str(permission).strip()
    }

def set_central_permissions(central_subject, selected_permissions):
    """
    Set the desired final permission state for a PIGS user in CentralAuth.

    Args:
        central_subject:
            CentralAuth User.public_id.

        selected_permissions:
            Iterable of permission codes that should be effectively granted.

    Returns:
        dict containing the CentralAuth response.
    """

    if not central_subject:
        raise CentralAuthPermissionError(
            "Central subject is required."
        )

    if not CENTRALAUTH_CLIENT_ID:
        raise CentralAuthPermissionError(
            "CENTRALAUTH_CLIENT_ID is not configured."
        )

    if not CENTRALAUTH_CLIENT_SECRET:
        raise CentralAuthPermissionError(
            "CENTRALAUTH_CLIENT_SECRET is not configured."
        )

    selected = sorted(
        {
            str(permission).strip().lower()
            for permission in selected_permissions
            if str(permission).strip()
        }
    )

    payload_items = [
        ("client_id", CENTRALAUTH_CLIENT_ID),
        ("client_secret", CENTRALAUTH_CLIENT_SECRET),
        ("sub", str(central_subject)),
    ]

    payload_items.extend(
        ("permissions", permission)
        for permission in selected
    )

    payload = urllib.parse.urlencode(
        payload_items
    ).encode("utf-8")

    request = urllib.request.Request(
        CENTRALAUTH_PERMISSION_OVERRIDE_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": (
                "application/x-www-form-urlencoded"
            ),
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=5,
        ) as response:

            response_data = json.loads(
                response.read().decode("utf-8")
            )

    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:

        raise CentralAuthPermissionError(
            "Could not update permissions in CentralAuth."
        ) from exc

    if response.status != 200:
        raise CentralAuthPermissionError(
            (
                "CentralAuth permission update failed "
                f"with HTTP {response.status}."
            )
        )

    effective_permissions = response_data.get(
        "effective_permissions",
        [],
    )

    if not isinstance(effective_permissions, list):
        raise CentralAuthPermissionError(
            "CentralAuth returned an invalid effective_permissions payload."
        )

    return {
        "system": response_data.get("system"),
        "sub": response_data.get("sub"),
        "effective_permissions": {
            str(permission).strip().lower()
            for permission in effective_permissions
            if str(permission).strip()
        },
        "granted_overrides": response_data.get(
            "granted_overrides",
            0,
        ),
        "revoked_overrides": response_data.get(
            "revoked_overrides",
            0,
        ),
    }