from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("personal-feed.json", views.personal_feed_json, name="personal_feed_json"),
    path("research-sentiment.json", views.research_sentiment_json, name="research_sentiment_json"),
    path("official-filings.json", views.official_filings_json, name="official_filings_json"),
    path("research/registry.json", views.research_registry_json, name="research_registry_json"),
    path("research/runs/", views.research_run_create, name="research_run_create"),
    path("research/runs/<str:run_id>.json", views.research_run_json, name="research_run_json"),
    path("research/runs/<str:run_id>/cancel/", views.research_run_cancel, name="research_run_cancel"),
    path("research/runs/<str:run_id>/ledger.csv", views.research_run_ledger, name="research_run_ledger"),
    path("personal-feed/timeline.json", views.personal_timeline_json, name="personal_timeline_json"),
    path("personal-feed/quote-selection/", views.personal_quote_selection, name="personal_quote_selection"),
    path("personal-feed/news-interest/", views.personal_news_interest, name="personal_news_interest"),
    path("personal-feed/event-preferences/", views.personal_event_preferences, name="personal_event_preferences"),
    path("personal-feed/analysis-priority/", views.personal_analysis_priority, name="personal_analysis_priority"),
    path("accounts/login/", views.PortalLoginView.as_view(), name="login"),
    path("accounts/logout/", views.PortalLogoutView.as_view(), name="logout"),
    path("accounts/register/", views.invite_register, name="invite_register"),
    path("watchlist/add/", views.watchlist_add, name="watchlist_add"),
    path("watchlist/<int:pk>/remove/", views.watchlist_remove, name="watchlist_remove"),
    path("settings/", views.user_settings, name="user_settings"),
    path("language/", views.change_language, name="change_language"),
]
