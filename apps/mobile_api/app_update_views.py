"""Public, cache-safe Android release metadata for Home Management.

The APK itself is hosted on a dedicated HTTPS object store (Cloudflare R2 in
production).  Django exposes only signed-release metadata; it never serves a
secret or accepts a client supplied download URL.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AndroidRelease:
    version_name: str
    version_code: int
    minimum_version_code: int
    download_url: str
    sha256: str
    release_notes: str
    published_at: str

    @classmethod
    def from_settings(cls):
        try:
            version_code = int(
                getattr(settings, "BIZMANAGER_ANDROID_LATEST_VERSION_CODE", 0)
            )
            minimum_version_code = int(
                getattr(settings, "BIZMANAGER_ANDROID_MINIMUM_VERSION_CODE", 0)
            )
        except (TypeError, ValueError) as error:
            raise ImproperlyConfigured(
                "Android release version codes must be integers."
            ) from error

        version_name = str(
            getattr(settings, "BIZMANAGER_ANDROID_LATEST_VERSION_NAME", "")
        ).strip()
        download_url = str(
            getattr(settings, "BIZMANAGER_ANDROID_APK_URL", "")
        ).strip()
        sha256 = str(
            getattr(settings, "BIZMANAGER_ANDROID_APK_SHA256", "")
        ).strip().lower()
        release_notes = str(
            getattr(settings, "BIZMANAGER_ANDROID_RELEASE_NOTES", "")
        ).strip()
        published_at = str(
            getattr(settings, "BIZMANAGER_ANDROID_RELEASE_PUBLISHED_AT", "")
        ).strip()

        parsed_url = urlsplit(download_url)
        if (
            version_code < 1
            or minimum_version_code < 1
            or minimum_version_code > version_code
            or not version_name
            or parsed_url.scheme != "https"
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
            or not _SHA256_RE.fullmatch(sha256)
        ):
            raise ImproperlyConfigured(
                "Home Management Android release metadata is invalid."
            )

        return cls(
            version_name=version_name,
            version_code=version_code,
            minimum_version_code=minimum_version_code,
            download_url=download_url,
            sha256=sha256,
            release_notes=release_notes,
            published_at=published_at,
        )


def _response(payload, *, status=200):
    return JsonResponse(
        payload,
        status=status,
        headers={"Cache-Control": "no-store, private"},
    )


@never_cache
@require_GET
def android_release(request):
    """Return the reviewed APK release policy without requiring a session."""
    if not getattr(settings, "BIZMANAGER_ANDROID_UPDATE_ENABLED", False):
        return _response(
            {
                "ok": True,
                "platform": "android",
                "channel": "apk",
                "enabled": False,
            }
        )

    try:
        release = AndroidRelease.from_settings()
    except ImproperlyConfigured:
        # Never leak deployment values or parser details to a public client.
        return _response(
            {
                "ok": False,
                "code": "release_metadata_unavailable",
                "message": "The update service is temporarily unavailable.",
            },
            status=503,
        )

    return _response(
        {
            "ok": True,
            "platform": "android",
            "channel": "apk",
            "enabled": True,
            "release": {
                "versionName": release.version_name,
                "versionCode": release.version_code,
                "minimumVersionCode": release.minimum_version_code,
                "downloadUrl": release.download_url,
                "sha256": release.sha256,
                "releaseNotes": release.release_notes,
                "publishedAt": release.published_at,
            },
        }
    )
