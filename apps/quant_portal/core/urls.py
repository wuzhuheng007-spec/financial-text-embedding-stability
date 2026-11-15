from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("accounts/login/", views.PortalLoginView.as_view(), name="login"),
    path("accounts/logout/", views.PortalLogoutView.as_view(), name="logout"),
    path("accounts/register/", views.invite_register, name="invite_register"),
    path("watchlist/add/", views.watchlist_add, name="watchlist_add"),
    path("watchlist/<int:pk>/remove/", views.watchlist_remove, name="watchlist_remove"),
    path("settings/", views.user_settings, name="user_settings"),
]
