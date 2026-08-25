from django.http import HttpResponse


SERVICE_WORKER_JS = """
const CACHE_NAME = "pig-management-v1";

self.addEventListener("install", (event) => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        self.clients.claim()
    );
});

self.addEventListener("fetch", (event) => {
    // Kwa sasa tunaruhusu requests ziende moja kwa moja server.
    // Hii inalinda login, forms, CSRF na Django dynamic data.
});
"""


def service_worker(request):
    response = HttpResponse(
        SERVICE_WORKER_JS,
        content_type="application/javascript"
    )

    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"

    return response
