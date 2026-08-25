from django.http import HttpResponse


SERVICE_WORKER_JS = """
const CACHE_NAME = "pig-management-v1";

const STATIC_ASSETS = [
    "/static/manifest.json",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png"
];


/*
============================================================
INSTALL
============================================================
*/

self.addEventListener("install", function (event) {

    event.waitUntil(

        caches.open(CACHE_NAME).then(function (cache) {

            return cache.addAll(STATIC_ASSETS);

        })

    );

    self.skipWaiting();

});


/*
============================================================
ACTIVATE
============================================================
*/

self.addEventListener("activate", function (event) {

    event.waitUntil(

        caches.keys().then(function (cacheNames) {

            return Promise.all(

                cacheNames
                    .filter(function (cacheName) {

                        return cacheName !== CACHE_NAME;

                    })
                    .map(function (cacheName) {

                        return caches.delete(cacheName);

                    })

            );

        })

    );

    self.clients.claim();

});


/*
============================================================
FETCH
============================================================
*/

self.addEventListener("fetch", function (event) {

    /*
     * Only handle GET requests.
     *
     * POST, PUT, PATCH and DELETE requests are always
     * handled directly by Django.
     */

    if (event.request.method !== "GET") {
        return;
    }


    /*
     * Let Django handle authenticated and dynamic pages
     * normally.
     *
     * We only use the network-first strategy here so that
     * fresh Django data is always preferred.
     */

    event.respondWith(

        fetch(event.request)

            .then(function (response) {

                return response;

            })

            .catch(function () {

                return caches.match(event.request);

            })

    );

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