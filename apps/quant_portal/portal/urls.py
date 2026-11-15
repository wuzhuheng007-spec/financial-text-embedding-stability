from django.urls import include, path

from core.views import healthz, readyz

urlpatterns = [
    # The admin login would bypass the portal's database-backed rate limiter.
    # V1 operators use local management commands; no admin HTTP route is mounted.
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    path("", include("core.urls")),
]
