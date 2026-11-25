from django.conf import settings
from django.conf.urls.static import static
"""
Main URL configuration for Home Management.
"""

from django.contrib import admin
from django.urls import path, include

from .pwa import service_worker


urlpatterns = [
    path("api/mobile/v1/", include("apps.mobile_api.urls")),


    path(
        "service-worker.js",
        service_worker,
        name="service_worker"
    ),

    path(
        "admin/",
        admin.site.urls
    ),

    path(
        "accounts/",
        include("apps.users.urls")
    ),

    path(
        "",
        include("apps.pigs.urls")
    ),

]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )

# Cloud release marker (no private information).
from django.http import JsonResponse
from django.views.decorators.http import require_GET
@require_GET
def cloud_release_marker(request):
    return JsonResponse({'release': 'M11-premium-cloud-1'}, headers={'Cache-Control':'no-store'})
urlpatterns.insert(0, path('api/mobile/v1/health/release/', cloud_release_marker))
