"""Invite-only UI. User requests never trigger market-data or model calls."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.http import HttpResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .forms import EmailAuthenticationForm, InviteRegistrationForm, UserPreferencesForm, WatchlistItemForm
from .models import Invitation, ProviderState, PublicFactCard, PublicMarketSnapshot, UserPreferences, WatchlistItem
from .rate_limit import clear_account_failure, is_blocked, record_failure
from .personal_feed_cache import for_owner


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


def readyz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return HttpResponse("database unavailable", status=503, content_type="text/plain")
    return HttpResponse("ready", content_type="text/plain")


class PortalLoginView(LoginView):
    template_name = "registration/login.html"
    authentication_form = EmailAuthenticationForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        email = request.POST.get("username", "")[:150]
        if is_blocked(request, email):
            return HttpResponse("Too many login attempts. Try again later.", status=429, content_type="text/plain")
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        clear_account_failure(form.cleaned_data["username"])
        return super().form_valid(form)

    def form_invalid(self, form):
        record_failure(self.request, self.request.POST.get("username", "")[:150])
        return super().form_invalid(form)


class PortalLogoutView(LogoutView):
    # Django 5 LogoutView accepts POST only and flushes the active session.
    next_page = "login"


@ensure_csrf_cookie
def invite_register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = InviteRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        digest = Invitation.digest(form.cleaned_data["invite_code"])
        try:
            with transaction.atomic():
                invite = Invitation.objects.select_for_update().get(token_digest=digest, email=email)
                if not invite.usable:
                    raise Invitation.DoesNotExist
                user = get_user_model().objects.create_user(
                    username=email, email=email, password=form.cleaned_data["password1"], is_staff=False,
                )
                invite.consumed_at = timezone.now()
                invite.save(update_fields=("consumed_at",))
                UserPreferences.objects.create(user=user)
                if settings.PERSONAL_MODE:
                    WatchlistItem.objects.bulk_create([
                        WatchlistItem(user=user, symbol=symbol, asset_type=asset_type)
                        for symbol, asset_type in (
                            ("TQQQ", "ETF"), ("SOXX", "ETF"), ("SPY", "ETF"),
                            ("QQQ", "ETF"), ("NVDA", "EQUITY"), ("MU", "EQUITY"),
                        )
                    ])
        except (Invitation.DoesNotExist, IntegrityError):
            form.add_error("invite_code", "Invalid or expired invitation.")
        else:
            login(request, user)
            return redirect("dashboard")
    return render(request, "registration/register.html", {"form": form})


@login_required
def dashboard(request):
    watchlist = list(WatchlistItem.objects.filter(user=request.user))
    symbols = [item.symbol for item in watchlist]
    snapshots = {}
    if symbols and settings.PUBLIC_MARKET_DATA_ENABLED:
        for row in PublicMarketSnapshot.objects.filter(
            symbol__in=symbols, display_approved=True,
            source_class=PublicMarketSnapshot.SourceClass.LICENSED,
        ).exclude(rights_basis="").order_by("symbol", "-provider_time"):
            snapshots.setdefault(row.symbol, row)
    shared_cards = Q(
        display_approved=True,
        visibility_scope="SHARED_APPROVED",
        source_class__in=(PublicFactCard.SourceClass.OFFICIAL, PublicFactCard.SourceClass.DEMO),
    ) & ~Q(rights_basis="")
    personal_cards = Q(
        visibility_scope="LOCAL_ONLY",
        source_class=PublicFactCard.SourceClass.OFFICIAL,
        evidence_state="CODEX_REVIEW_DEVELOPMENT",
        source_review_status="CODEX_REVIEW_SOURCE_SUPPORTED",
    )
    card_filter = shared_cards | personal_cards if settings.PERSONAL_MODE else shared_cards
    eligible_cards = PublicFactCard.objects.filter(card_filter)
    cards = list(eligible_cards.order_by("-source_date", "issuer", "metric")[:30])
    actual_cards = [card for card in cards if card.fact_kind == PublicFactCard.FactKind.ACTUAL]
    guidance_cards = [card for card in cards if card.fact_kind == PublicFactCard.FactKind.GUIDANCE]
    missing_free_cash_flow = []
    if settings.PERSONAL_MODE:
        for issuer in ("NVIDIA", "Micron Technology"):
            if not eligible_cards.filter(
                issuer=issuer,
                fact_kind=PublicFactCard.FactKind.ACTUAL,
                fact_topic="free_cash_flow",
                source_review_status="CODEX_REVIEW_SOURCE_SUPPORTED",
            ).exists():
                missing_free_cash_flow.append(issuer)
    states = {name: {"state": "NOT_CONNECTED", "last_success_at": None} for name in ("moomoo", "jin10")}
    for row in ProviderState.objects.filter(provider__in=states):
        states[row.provider] = {"state": row.state, "last_success_at": row.last_success_at}
    return render(request, "core/dashboard.html", {
        "watchlist": watchlist,
        "watch_rows": [{"item": item, "snapshot": snapshots.get(item.symbol)} for item in watchlist],
        "snapshots": snapshots,
        "fact_cards": cards,
        "actual_fact_cards": actual_cards,
        "guidance_fact_cards": guidance_cards,
        "missing_free_cash_flow": missing_free_cash_flow,
        "provider_states": states,
        "watchlist_form": WatchlistItemForm(),
        "display_timezone": request.display_timezone,
        "personal_mode": settings.PERSONAL_MODE,
        "personal_feed_enabled": (settings.PERSONAL_MODE and
                                  request.user.username == settings.PERSONAL_FEED_OWNER_USERNAME),
    })


@login_required
def personal_feed_json(request):
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Personal feed unavailable")
    symbols = set(WatchlistItem.objects.filter(user=request.user).values_list("symbol", flat=True))
    data = for_owner(request.user, symbols)
    response = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def watchlist_add(request):
    form = WatchlistItemForm(request.POST)
    if form.is_valid():
        item = form.save(commit=False)
        item.user = request.user
        try:
            item.full_clean()
            item.save()
        except (IntegrityError, ValidationError):
            # Keep errors generic; neither another user's list nor DB details are exposed.
            messages.error(request, "Invalid or duplicate watchlist item.")
        else:
            messages.success(request, "Watchlist item added as an unverified security candidate.")
    else:
        messages.error(request, "Invalid watchlist item.")
    return redirect("dashboard")


@login_required
@require_POST
def watchlist_remove(request, pk):
    item = get_object_or_404(WatchlistItem, pk=pk, user=request.user)
    item.delete()
    messages.success(request, "Watchlist item removed.")
    return redirect("dashboard")


@login_required
def user_settings(request):
    prefs, _ = UserPreferences.objects.get_or_create(user=request.user)
    form = UserPreferencesForm(request.POST or None, instance=prefs)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("user_settings")
    return render(request, "core/settings.html", {"form": form})
