from django.conf import settings


def deployment(request):
    """Where this portal runs, for labels on every page (local Windows or private cloud)."""
    return {"deployment_kind": settings.DEPLOYMENT_KIND,
            "is_cloud_deployment": settings.DEPLOYMENT_KIND == "cloud_private"}
