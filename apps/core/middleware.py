from django.utils.deprecation import MiddlewareMixin


class NoCacheAuthenticatedMiddleware(MiddlewareMixin):

    def process_response(self, request, response):

        if (
            request.user.is_authenticated
            or request.path.startswith("/accounts/login/")
            or request.path.startswith("/accounts/logout/")
        ):

            response["Cache-Control"] = (
                "no-cache, no-store, must-revalidate, max-age=0"
            )

            response["Pragma"] = "no-cache"
            response["Expires"] = "0"

        return response