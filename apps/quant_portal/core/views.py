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
from django.utils.http import url_has_allowed_host_and_scheme

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
def research_sentiment_json(request):
    """Owner-only read of the separate local v4 cache; no supplier/model call."""
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Research sentiment unavailable")
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from .personal_feed_cache import REPO_ROOT

    db = REPO_ROOT / "artifacts/news_sentiment_factor_v4_20261117/news_research.sqlite3"
    data = {"state": "NO_RESEARCH_CACHE", "article_versions": 0, "english_titles": 0,
            "prospective_versions": 0, "completed_scores": 0, "qualified_prospective_scores": 0, "rows": [],
            "meaning": "text tone only; not a return forecast or independently verified news"}
    if db.is_file():
        try:
            with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)) as con:
                data.update({
                    "state": "READY",
                    "article_versions": con.execute("SELECT COUNT(*) FROM articles").fetchone()[0],
                    "english_titles": con.execute("SELECT COUNT(*) FROM articles WHERE language='ENGLISH_CANDIDATE'").fetchone()[0],
                    "prospective_versions": con.execute("SELECT COUNT(*) FROM articles WHERE observation_mode='PROSPECTIVE'").fetchone()[0],
                    "completed_scores": con.execute("SELECT COUNT(*) FROM scores WHERE status='DONE'").fetchone()[0],
                    "qualified_prospective_scores": con.execute("SELECT COUNT(*) FROM scores WHERE status='DONE' AND score_origin='PROSPECTIVE_RECEIPT'").fetchone()[0],
                })
                data["rows"] = [{"provider": r[0], "publisher": r[1], "score": r[2],
                                  "scored_at_utc": r[3], "score_origin": r[4]}
                    for r in con.execute("""SELECT s.provider,a.publisher,s.net_score,s.scored_at_utc,s.score_origin
                        FROM scores s JOIN articles a ON (a.provider=s.provider AND a.article_id=s.article_id
                        AND a.version=s.version) WHERE s.status='DONE' AND s.input_kind='TITLE'
                        ORDER BY s.scored_at_utc DESC LIMIT 10""")]
        except sqlite3.Error:
            data["state"] = "CACHE_UNAVAILABLE"
            data["rows"] = []
    response = JsonResponse(data)
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def personal_analysis_priority(request):
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Personal analysis unavailable")
    import re
    import sqlite3
    from .personal_feed_cache import REPO_ROOT
    key=request.POST.get("item_key", "")
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        return JsonResponse({"result":"INVALID_ITEM"},status=400)
    path=REPO_ROOT / "apps/quant_portal/var/personal_analysis_v16.sqlite3"
    if not path.is_file():
        return JsonResponse({"result":"ANALYSIS_UNAVAILABLE"},status=503)
    action=request.POST.get("action", "prioritize")
    if action not in {"prioritize", "retry"}:
        return JsonResponse({"result":"INVALID_ACTION"},status=400)
    from scripts.quant_personal_feed.analysis_v16 import cfg
    contract=cfg()
    identity=(contract["model_revision"],contract["prompt_version"],contract["schema_version"])
    with sqlite3.connect(path,timeout=5) as db:
        if action == "retry":
            row=db.execute("""SELECT version,status FROM analysis_jobs WHERE owner_id=? AND item_key=?
              AND model_revision=? AND prompt_version=? AND schema_version=?
              ORDER BY version DESC LIMIT 1""",
              (request.user.pk,key,*identity)).fetchone()
            if row is None or row[1] != "FALLBACK":
                result_code="NO_RETRYABLE_JOB"
            else:
                used=db.execute("""SELECT COUNT(*) FROM analysis_attempts WHERE owner_id=?
                  AND item_key=? AND version=? AND prompt_version=?""",
                  (request.user.pk,key,row[0],contract["prompt_version"])).fetchone()[0]
                if used >= 3:
                    result_code="ATTEMPT_LIMIT_REACHED"
                else:
                    db.execute("""UPDATE analysis_jobs SET status='PENDING',priority=MAX(priority,5000),
                      error_class=NULL WHERE owner_id=? AND item_key=? AND version=?
                      AND model_revision=? AND prompt_version=? AND schema_version=?""",
                      (request.user.pk,key,row[0],*identity))
                    result_code="RETRY_QUEUED"
        else:
            result=db.execute("""UPDATE analysis_jobs SET priority=MAX(priority,5000)
              WHERE owner_id=? AND item_key=? AND model_revision=? AND prompt_version=?
              AND schema_version=? AND status='PENDING'""",(request.user.pk,key,*identity))
            result_code="PRIORITIZED" if result.rowcount else "NO_PENDING_JOB"
    response=JsonResponse({"result":result_code})
    response["Cache-Control"]="private, no-store"
    return response


@login_required
def personal_timeline_json(request):
    """Read only the bound personal cache; page refresh never calls a supplier."""
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Personal timeline unavailable")
    import sys
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.quant_personal_feed.timeline import read_timeline
    try:
        data = read_timeline(settings.PERSONAL_FEED_DB_PATH, request.user.pk,
            symbol=request.GET.get("symbol", "").strip().upper(),
            session=request.GET.get("session", "ALL"),
            range_key=request.GET.get("range", "2h"),
            end_at=request.GET.get("end") or None)
    except ValueError:
        return JsonResponse({"error": "INVALID_TIMELINE_REQUEST"}, status=400)
    response = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def personal_quote_selection(request):
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Personal feed unavailable")
    symbol = str(request.POST.get("symbol", "")).strip().upper()
    action = str(request.POST.get("action", "")).strip()
    replace_symbol = str(request.POST.get("replace_symbol", "")).strip().upper() or None
    if not symbol or len(symbol) > 16 or not symbol.isascii() or not symbol.replace(".", "").isalnum():
        return JsonResponse({"result": "INVALID_SYMBOL"}, status=400)
    if action == "subscribe" and not WatchlistItem.objects.filter(user=request.user,symbol=symbol).exists():
        return JsonResponse({"result": "ADD_TO_WATCHLIST_FIRST"}, status=400)
    import json
    import sys
    from pathlib import Path
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0,str(REPO_ROOT))
    from scripts.quant_personal_feed.store import PersonalFeedStore
    from scripts.quant_personal_feed.collector import verified_current_symbols
    config = json.loads((REPO_ROOT / "configs/quant_portal_personal_feeds_v11_20261116.json").read_text(encoding="utf-8"))
    try:
        verified = verified_current_symbols(config)
        store = PersonalFeedStore(Path(settings.PERSONAL_FEED_DB_PATH),request.user.pk)
        try:
            store.ensure_selection(config["moomoo"]["priority_symbols"])
            result = store.change_selection(symbol,action,set(verified),
                                            config["moomoo"]["local_subscription_key_budget"],replace_symbol)
            if result == "PENDING_SUBSCRIBE":
                store.symbol_state(request.user.pk,symbol,"WAITING_SUBSCRIPTION","SERVER_ACK_PENDING")
                if replace_symbol:
                    store.symbol_state(request.user.pk,replace_symbol,"WAITING_STOP","REPLACEMENT_REQUESTED")
            elif result == "PENDING_STOP":
                store.symbol_state(request.user.pk,symbol,"WAITING_STOP","SERVER_UNSUBSCRIBE_PENDING")
        finally:
            store.close()
    except (OSError, ValueError, KeyError):
        return JsonResponse({"result": "MAPPING_OR_CACHE_UNAVAILABLE"}, status=503)
    response = JsonResponse({"result": result})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def personal_news_interest(request):
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Personal feed unavailable")
    key = str(request.POST.get("interest_key", "")).strip().upper()
    enabled = request.POST.get("enabled") == "1"
    import sys
    from pathlib import Path
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0,str(REPO_ROOT))
    from scripts.quant_personal_feed.store import PersonalFeedStore
    try:
        store = PersonalFeedStore(Path(settings.PERSONAL_FEED_DB_PATH),request.user.pk)
        try:
            store.set_news_interest(key,enabled)
        finally:
            store.close()
    except ValueError:
        return JsonResponse({"result": "INVALID_INTEREST"}, status=400)
    response = JsonResponse({"result":"UPDATED","interest_key":key,"enabled":enabled})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def personal_event_preferences(request):
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Personal feed unavailable")
    entities = request.POST.getlist("entity")
    categories = request.POST.getlist("category")
    if len(entities) > 30 or len(categories) > 20:
        return JsonResponse({"result": "INVALID_PREFERENCES"}, status=400)
    import sys
    from pathlib import Path
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.quant_personal_feed.store import PersonalFeedStore
    try:
        store = PersonalFeedStore(Path(settings.PERSONAL_FEED_DB_PATH),request.user.pk)
        try:
            store.set_event_preferences(entities,categories)
        finally:
            store.close()
    except ValueError:
        return JsonResponse({"result": "INVALID_PREFERENCES"}, status=400)
    response = JsonResponse({"result": "UPDATED"})
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
            messages.success(request, "已加入自选；行情订阅需另行确认。" if settings.PERSONAL_MODE
                             else "Watchlist item added as an unverified security candidate.")
    else:
        messages.error(request, "Invalid watchlist item.")
    return redirect("dashboard")


@login_required
@require_POST
def watchlist_remove(request, pk):
    item = get_object_or_404(WatchlistItem, pk=pk, user=request.user)
    if settings.PERSONAL_MODE and request.user.username == settings.PERSONAL_FEED_OWNER_USERNAME:
        feed = for_owner(request.user,set())
        selected = {row["symbol"] for row in feed.get("selection",[]) if row["desired"]}
        pending_stop = {row["symbol"] for row in feed.get("symbols",[])
                        if row["state"] == "WAITING_STOP"}
        if item.symbol in selected or item.symbol in pending_stop:
            messages.error(request,"请先停止该证券行情订阅，等待确认后再移除自选。")
            return redirect("dashboard")
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


@login_required
@require_POST
def change_language(request):
    language=request.POST.get("language")
    if language not in {"zh", "en"}:
        return JsonResponse({"result": "INVALID_LANGUAGE"}, status=400)
    prefs,_=UserPreferences.objects.get_or_create(user=request.user)
    prefs.display_language=language
    prefs.save(update_fields=["display_language"])
    target=request.POST.get("next") or "/"
    if not url_has_allowed_host_and_scheme(target,allowed_hosts={request.get_host()},require_https=False):
        target="/"
    return redirect(target)
