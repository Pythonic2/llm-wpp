from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers


class CorsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.headers.get("Origin")
        if request.method == "OPTIONS" and origin in settings.CORS_ALLOWED_ORIGINS:
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)
        if origin in settings.CORS_ALLOWED_ORIGINS:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Credentials"] = "true"
            response["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Content-Type, X-CSRFToken, Idempotency-Key"
            response["Access-Control-Max-Age"] = "600"
            patch_vary_headers(response, ("Origin",))
        return response
