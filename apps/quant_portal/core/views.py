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
    personal_owner = (settings.PERSONAL_MODE and
                      request.user.username == settings.PERSONAL_FEED_OWNER_USERNAME)
    card_filter = shared_cards | personal_cards if personal_owner else shared_cards
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
        "deployment_kind": settings.DEPLOYMENT_KIND,
        "provider_gates": settings.PROVIDER_GATES,
    })


@login_required
def personal_feed_json(request):
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Personal feed unavailable")
    symbols = set(WatchlistItem.objects.filter(user=request.user).values_list("symbol", flat=True))
    data = for_owner(request.user, symbols)
    data["provider_gates"] = {k: v for k, v in settings.PROVIDER_GATES.items() if v}
    data["deployment_kind"] = settings.DEPLOYMENT_KIND
    response = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def official_filings_json(request):
    """Owner-only, read-only SEC EDGAR filing metadata for the watchlist; never calls SEC."""
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Official filings unavailable")
    import sys
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.quant_cloud_official.sec_filings import read_for_portal
    symbols = set(WatchlistItem.objects.filter(user=request.user).values_list("symbol", flat=True))
    try:
        data = read_for_portal(settings.OFFICIAL_FILINGS_DB_PATH, symbols)
    except Exception as exc:  # noqa: BLE001 - an unreadable cache is a status, not a page error
        data = {"state": "READ_ERROR", "error_class": type(exc).__name__, "instruments": [], "filings": []}
    data["watchlist"] = sorted(symbols)
    response = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def research_sentiment_json(request):
    """Owner-only reads of separate local English v4 and Chinese v4.1 caches."""
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Research sentiment unavailable")
    import sqlite3
    from contextlib import closing
    from pathlib import Path
    from .personal_feed_cache import REPO_ROOT
    from .research_chinese_cache import chinese_research_summary

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
    data["chinese"] = chinese_research_summary(REPO_ROOT)
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
    if request.GET.get("range") == "1y":
        import sqlite3
        from scripts.quant_personal_feed.daily_history import read_daily_history
        try:
            history = read_daily_history(REPO_ROOT, request.GET.get("symbol", "").strip().upper())
        except ValueError:
            return JsonResponse({"error": "INVALID_TIMELINE_REQUEST"}, status=400)
        except sqlite3.Error:
            return JsonResponse({"error": "DAILY_HISTORY_UNAVAILABLE"}, status=503)
        response = JsonResponse(history)
        response["Cache-Control"] = "private, no-store"
        return response
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


# ---------- research workflow v1 (owner only; web never runs a backtest itself) ----------
import re as _re

_RUN_ID = _re.compile(r"^run-\d{8}T\d{6}Z-[0-9a-f]{6}$")
# Only protocols frozen in the repository can be selected; no user-supplied code or parameters.
RESEARCH_PROTOCOLS = {"mom20-daily-long-cash": "configs/quant_research_mom20_v1.json"}
# v2 protocols bound to a versioned engine (contracts in scripts/quant_research_v1/catalog.py ENGINES).
RESEARCH_PROTOCOLS_V2 = {"phase5_strategy_v1": "research/market_sentiment/phase5_strategy_v1/config.json"}
_IMPORT_ID = _re.compile(r"^ext-[a-z0-9_]+-\d{8}T\d{6}Z$")
_ARTIFACT_NAME = _re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _phase5_protocol(repo):
    import hashlib, json
    from scripts.quant_research_v1 import catalog
    cfg_path = repo / RESEARCH_PROTOCOLS_V2["phase5_strategy_v1"]
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    return {"protocol_id": "phase5_strategy_v1", "protocol_version": "1", "factor": {"factor_id": "phase5_strategy_v1", "factor_version": "1"},
            "engine": {"engine_id": "phase5_o2o_v1", "engine_version": "1"}, "evaluation_mode": "MARKET_TIME_SERIES",
            "benchmark_id": "MA200_SAME_INTERVALS (BUY_HOLD reported alongside)", "sample_mask": {"periods": cfg["periods"], "series": cfg["series"]},
            "frozen_config_sha256": hashlib.sha256(cfg_path.read_bytes()).hexdigest(), "frozen_files_sha256": catalog.phase5_files(repo),
            "freeze_commit": catalog.PHASE5_FREEZE_COMMIT, "mode": "exact_or_external_by_input_hash"}


def _ro_registry(registry):
    import sqlite3
    p = registry.root() / "registry.sqlite3"
    if not p.is_file():
        return None
    db = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, timeout=5); db.row_factory = sqlite3.Row
    return db


def _research_owner(request):
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("Research unavailable")
    import sys
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.quant_research_v1 import registry
    return registry, REPO_ROOT


def _latest_audit(root, run_id):
    """Most recent independent audit for a run: written by the run itself (audit_*.json) or a later
    re-audit kept under research/audits/<run_id>/. Older files are never modified."""
    import json
    if not _RUN_ID.match(run_id):
        return None
    found = list((root / "runs" / run_id).glob("audit_*.json")) + list((root / "audits" / run_id).glob("*.json"))
    best = None
    for f in found:
        try:
            a = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        item = {"verifier_version": a.get("verifier_version"), "ok": bool(a.get("ok")), "audited_at_utc": a.get("audited_at_utc"),
                "problems": (a.get("problems") or [])[:3], "file": f.relative_to(root).as_posix(),
                "provenance": a.get("provenance")}
        if best is None or (item["audited_at_utc"] or "") > (best["audited_at_utc"] or ""):
            best = item
    return best


def _no_store(response):
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def research_registry_json(request):
    registry, repo = _research_owner(request)
    import json
    try:
        data = registry.read_all()
    except Exception as exc:  # noqa: BLE001 - an unreadable registry is a status, not a page error
        data = {"state": "READ_ERROR", "error_class": type(exc).__name__, "datasets": [], "factors": [], "runs": []}
    protocols = []
    for pid, rel in RESEARCH_PROTOCOLS.items():
        cfg = json.loads((repo / rel).read_text(encoding="utf-8"))
        protocols.append({"protocol_id": pid, "protocol_version": cfg["protocol_version"],
                          "factor": cfg["factor"], "costs_bps": cfg["costs"]["scenarios_bps_one_way"],
                          "segments": cfg["segments"], "execution": cfg["execution"]["price"],
                          "return_label": cfg["price_series"]["return_label"]})
    from scripts.quant_research_v1 import catalog
    for p in protocols:
        p["engine"] = "legacy_mom20_v1@1"
    try:
        p5 = _phase5_protocol(repo)
    except (OSError, ValueError) as exc:  # frozen files missing from this image: protocol unavailable, page still works
        p5 = None; data["protocol_errors"] = [f"phase5_strategy_v1 unavailable: {type(exc).__name__}"]
    if p5: protocols.append({"protocol_id": p5["protocol_id"], "protocol_version": p5["protocol_version"], "factor": p5["factor"], "engine": "phase5_o2o_v1@1",
                      "costs_bps": json.loads((repo / RESEARCH_PROTOCOLS_V2["phase5_strategy_v1"]).read_text(encoding="utf-8"))["cost_bps_one_way"],
                      "periods": p5["sample_mask"]["periods"], "series": p5["sample_mask"]["series"], "frozen_config_sha256": p5["frozen_config_sha256"],
                      "benchmark_id": p5["benchmark_id"], "evaluation_mode": p5["evaluation_mode"]})  # noqa: E701
    data["protocols"] = protocols
    data["engines"] = {f"{k[0]}@{k[1]}": v for k, v in catalog.ENGINES.items()}
    data["admission_paused"] = registry.admission_paused()
    for r in data.get("runs", []):
        r["latest_audit"] = _latest_audit(registry.root(), r["run_id"])
    # Eligibility is decided here (backend), for every registered spec against each dataset.
    db = _ro_registry(registry)
    if db is not None:
        try:
            tabs = {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "factor_specs" in tabs:
                ds_keys = [(d["dataset_id"], d["dataset_version"]) for d in data.get("datasets", [])]
                for f in data.get("factors", []):
                    key = (f["factor_id"], f["factor_version"])
                    f["eligibility"] = {f"{d[0]}@{d[1]}": catalog.eligibility(db, d, key) for d in ds_keys} or {"(no dataset)": catalog.eligibility(db, None, key)}
                data["capabilities_effective"] = [{"engine": f"{k[0]}@{k[1]}", "workers": v} for k, v in catalog.capabilities(db).items()]
        finally:
            db.close()
    return _no_store(JsonResponse(data, json_dumps_params={"ensure_ascii": False, "default": str}))


@login_required
@require_POST
def research_run_rerun(request, run_id):
    """New run with the same dataset, factor and frozen config; linked as a retry if the original failed or was cancelled."""
    registry, _ = _research_owner(request)
    import json
    if not _RUN_ID.match(run_id):
        raise Http404()
    if registry.admission_paused():
        return JsonResponse({"result": "PAUSED", "reason": "研究入队已暂停（发布或回滚维护中）"}, status=503)
    db = registry.connect()
    try:
        row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise Http404()
        if row["status"] in ("QUEUED", "RUNNING"):
            return JsonResponse({"result": "REJECTED", "reason": "run still active"}, status=400)
        from scripts.quant_research_v1 import catalog
        eng = registry.engine_of(row); attempt = run_id  # every rerun is a new run linked to its source run
        if eng == registry.LEGACY_ENGINE:
            new_id = registry.create_run(db, (row["dataset_id"], row["dataset_version"]), (row["factor_id"], row["factor_version"]),
                                         json.loads(row["config_json"]), requested_by=request.user.username, attempt_of=attempt)
        else:
            new_id = catalog.create_run_v2(db, (row["dataset_id"], row["dataset_version"]), (row["factor_id"], row["factor_version"]),
                                           json.loads(row["config_json"]), eng, requested_by=request.user.username, attempt_of=attempt)
    except (LookupError, ValueError) as exc:
        return JsonResponse({"result": "REJECTED", "reason": str(exc)}, status=400)
    finally:
        db.close()
    return _no_store(JsonResponse({"result": "QUEUED", "run_id": new_id, "attempt_of": run_id}))


@login_required
@require_POST
def research_run_create(request):
    registry, repo = _research_owner(request)
    import json
    if registry.admission_paused():
        return JsonResponse({"result": "PAUSED", "reason": "研究入队已暂停（发布或回滚维护中）"}, status=503)
    ds = (request.POST.get("dataset") or "").split("@", 1)
    fv = (request.POST.get("factor") or "").split("@", 1)
    pid = request.POST.get("protocol") or ""
    if len(ds) != 2 or len(fv) != 2 or pid not in {**RESEARCH_PROTOCOLS, **RESEARCH_PROTOCOLS_V2}:
        return JsonResponse({"result": "INVALID_SELECTION"}, status=400)
    db = registry.connect()
    try:
        if pid in RESEARCH_PROTOCOLS_V2:
            from scripts.quant_research_v1 import catalog
            try:
                protocol = _phase5_protocol(repo)
            except (OSError, ValueError):
                return JsonResponse({"result": "REJECTED", "reason": "PROTOCOL_UNAVAILABLE: frozen phase5 files missing from this image"}, status=400)
            if tuple(fv) != (protocol["factor"]["factor_id"], protocol["factor"]["factor_version"]):
                return JsonResponse({"result": "REJECTED", "reason": "protocol is frozen for a different strategy"}, status=400)
            run_id = catalog.create_run_v2(db, tuple(ds), tuple(fv), protocol, ("phase5_o2o_v1", "1"), requested_by=request.user.username)
        else:
            protocol = json.loads((repo / RESEARCH_PROTOCOLS[pid]).read_text(encoding="utf-8"))
            run_id = registry.create_run(db, tuple(ds), tuple(fv), protocol, requested_by=request.user.username)
    except (LookupError, ValueError) as exc:
        return JsonResponse({"result": "REJECTED", "reason": str(exc)}, status=400)
    finally:
        db.close()
    return _no_store(JsonResponse({"result": "QUEUED", "run_id": run_id}))


@login_required
@require_POST
def research_run_cancel(request, run_id):
    registry, _ = _research_owner(request)
    if not _RUN_ID.match(run_id):
        raise Http404()
    db = registry.connect()
    try:
        status = registry.request_cancel(db, run_id)
    except LookupError:
        raise Http404()
    finally:
        db.close()
    return _no_store(JsonResponse({"result": status}))


def _run_dir(registry, run_id):
    if not _RUN_ID.match(run_id):
        raise Http404()
    path = registry.root() / "runs" / run_id
    if not path.is_dir():
        raise Http404()
    return path


@login_required
def research_run_json(request, run_id):
    registry, _ = _research_owner(request)
    import json
    rows = [r for r in registry.read_all()["runs"] if r["run_id"] == run_id]
    if not rows:
        raise Http404()
    data = {"run": rows[0]}
    path = registry.root() / "runs" / run_id
    if _RUN_ID.match(run_id) and path.is_dir():
        for name in ("metrics", "curves", "verify"):
            f = path / f"{name}.json"
            data[name] = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
        data["latest_audit"] = _latest_audit(registry.root(), run_id)
        data["errata"] = None
        from .personal_feed_cache import REPO_ROOT
        er = REPO_ROOT / "configs/quant_research_mom20_v1.ERRATA.json"
        if er.is_file() and rows[0]["protocol_id"] == "mom20-daily-long-cash":
            e = json.loads(er.read_text(encoding="utf-8"))
            if run_id in e["applies_to"]["runs"] or rows[0].get("config_sha256") == e["applies_to"]["config_sha256"]:
                data["errata"] = {"erratum_id": e["erratum_id"],
                                  "corrections": e.get("corrections_zh") or [c["correct"] for c in e["corrections"]],
                                  "corrections_en": [c["correct"] for c in e["corrections"]]}
        log = path / "run.log"
        data["log_tail"] = log.read_text(encoding="utf-8").splitlines()[-30:] if log.is_file() else []
        rep = path / "report.json"  # phase5_o2o_v1 summary for the page (full numbers stay in the artifacts)
        data["report"] = json.loads(rep.read_text(encoding="utf-8")) if rep.is_file() else None
    return _no_store(JsonResponse(data, json_dumps_params={"ensure_ascii": False}))


@login_required
def research_capabilities_json(request):
    """Engines and the capabilities that job images actually registered (never self-declared by the browser)."""
    registry, _ = _research_owner(request)
    from scripts.quant_research_v1 import catalog
    out = {"engines": {f"{k[0]}@{k[1]}": v for k, v in catalog.ENGINES.items()}, "registered": []}
    db = _ro_registry(registry)
    if db is not None:
        try:
            out["registered"] = [{"engine": f"{k[0]}@{k[1]}", "workers": v} for k, v in catalog.capabilities(db).items()]
        finally:
            db.close()
    return _no_store(JsonResponse(out, json_dumps_params={"ensure_ascii": False}))


@login_required
def research_compare_json(request):
    registry, _ = _research_owner(request)
    from scripts.quant_research_v1 import catalog
    a, b = request.GET.get("a", ""), request.GET.get("b", "")
    if not (_RUN_ID.match(a) and _RUN_ID.match(b)):
        raise Http404()
    db = _ro_registry(registry)
    if db is None:
        raise Http404()
    try:
        res = catalog.compare(db, a, b)
    except LookupError:
        raise Http404()
    finally:
        db.close()
    return _no_store(JsonResponse({"a": a, "b": b, **res}, json_dumps_params={"ensure_ascii": False}))


@login_required
def research_import_json(request, import_id):
    registry, _ = _research_owner(request)
    if not _IMPORT_ID.match(import_id):
        raise Http404()
    rows = [x for x in registry.read_all().get("external_runs", []) if x["import_id"] == import_id]
    if not rows:
        raise Http404()
    return _no_store(JsonResponse(rows[0], json_dumps_params={"ensure_ascii": False}))


@login_required
def research_run_artifact(request, run_id, name):
    """Download a REGISTERED artifact of an owner run: name must be in the run's artifact list; the resolved
    path must stay inside that run directory (no traversal, no other run's files)."""
    registry, _ = _research_owner(request)
    path = _run_dir(registry, run_id)
    if not _ARTIFACT_NAME.match(name) or name in (".", ".."):
        raise Http404()
    db = _ro_registry(registry)
    if db is None:
        raise Http404()
    try:
        row = db.execute("SELECT rel_path, sha256 FROM artifacts WHERE run_id=? AND name=?", (run_id, name)).fetchone()
    finally:
        db.close()
    if row is None:
        raise Http404()
    f = (path / name).resolve()
    if f.parent != path.resolve() or not f.is_file() or row["rel_path"] != f"runs/{run_id}/{name}":
        raise Http404()
    response = HttpResponse(f.read_bytes(), content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{run_id}_{name}"'
    response["X-Content-SHA256"] = row["sha256"]
    return _no_store(response)


@login_required
def research_run_ledger(request, run_id):
    registry, _ = _research_owner(request)
    path = _run_dir(registry, run_id)
    bps = request.GET.get("bps", "0")
    if bps not in {"0", "5", "10"}:
        raise Http404()
    f = path / f"ledger_cost{bps}bps.csv"
    if not f.is_file():
        raise Http404()
    response = HttpResponse(f.read_bytes(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{run_id}_ledger_cost{bps}bps.csv"'
    return _no_store(response)


# ---------- X keyword stance (owner only; reads the study's private store, never calls a provider or a model) ----------
@login_required
def x_stance_json(request):
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("X stance unavailable")
    import os
    import sys
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from scripts.x_stance_v1 import config as x_config, portal_read
        ident = (request.GET.get("identity") or "").strip()[:64]
        data = portal_read.read_for_portal(x_config.load(), env=dict(os.environ), simple_identity=ident if ident.isalnum() else None)
    except Exception as exc:  # noqa: BLE001 - an unreadable store is a status, not a page error
        data = {"state": "READ_ERROR", "error_class": type(exc).__name__, "has_data": False}
    return _no_store(JsonResponse(data))


@login_required
@require_POST
def x_stance_vocabulary(request):
    """Owner-only keyword edit. Creates a new immutable vocabulary version that takes effect with the next batch;
    it never changes a frozen plan, never changes the budget and sends nothing to any provider."""
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("X stance unavailable")
    import json
    import sys
    from datetime import datetime, timezone as dt_timezone
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
        if not isinstance(body, dict):                       # a JSON list or string is a bad request, not a server error
            raise ValueError
        changes = body.get("changes")
        if not isinstance(changes, list) or not (1 <= len(changes) <= 20) or not all(isinstance(c, dict) for c in changes):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return _no_store(JsonResponse({"result": "REJECTED", "reason": "BAD_REQUEST"}, status=400))
    from scripts.x_stance_v1 import config as x_config, store as x_store, vocab as x_vocab
    clean = [{k: c.get(k) for k in ("action", "topic_id", "keyword", "new_keyword") if c.get(k) is not None} for c in changes]
    try:
        db = x_store.connect()
        try:
            version = x_vocab.propose(db, x_config.load(), clean, datetime.now(dt_timezone.utc))
            overview = x_vocab.overview(db, x_config.load())
        finally:
            db.close()
    except x_vocab.VocabError as exc:
        return _no_store(JsonResponse({"result": "REJECTED", "reason": str(exc)}, status=400))
    except Exception as exc:  # noqa: BLE001 - a store that cannot be written is a status, not a page error
        return _no_store(JsonResponse({"result": "ERROR", "error_class": type(exc).__name__}, status=503))
    return _no_store(JsonResponse({"result": "PENDING_VERSION_CREATED", "version": version["version"], "effective_from_ny_date": version["effective_from_ny_date"],
                                   "impact": version["impact"], "vocabulary": overview}))


# ---------- real-time news v1 (owner only; reads the collector's cache, never calls a provider) ----------
_MACRO_CHANNELS = {"economics", "economic data", "macro economic events", "federal reserve", "fed", "treasuries", "bonds",
                   "government", "politics", "global", "commodities", "forex", "inflation", "interest rates"}
_ETF_WATCH = {"TQQQ", "QQQ", "SOXX", "SPY"}


@login_required
def news_json(request):
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("News unavailable")
    import json
    import sys
    from datetime import datetime, timezone
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.quant_news_v1.store import read_for_portal
    try:
        data = read_for_portal(limit=200, english_only=True)
    except Exception as exc:  # noqa: BLE001 - an unreadable cache is a status, not a page error
        data = {"state": "READ_ERROR", "error_class": type(exc).__name__, "items": [], "providers": []}
    watch = set(WatchlistItem.objects.filter(user=request.user).values_list("symbol", flat=True))
    for it in data["items"]:
        syms = set(it.get("symbols") or [])
        chans = {c.lower() for c in it.get("channels") or []}
        it["relevance"] = {"direct_watch": sorted(syms & watch), "direct_watch_etf": sorted(syms & watch & _ETF_WATCH),
                           "macro": bool(chans & _MACRO_CHANNELS),
                           "note": "直接提及=供应商标注的代码；宏观=供应商频道；无版本化成分数据，不推断 ETF 专属。"}
        if not it.get("body"):
            it["body"] = None
    now = datetime.now(timezone.utc)
    active = [p for p in data["providers"] if p["provider"] != "unconfigured"]
    prov = max(active, key=lambda p: p.get("updated_at") or "", default=None)
    label, kind = "新闻源未连接", "off"
    if prov:
        hb = prov.get("last_heartbeat_at")
        try:
            age = (now - datetime.fromisoformat(hb.replace("Z", "+00:00"))).total_seconds() if hb else None
        except ValueError:
            age = None
        st = prov["state"]
        delivered = any(i.get("available_at") for i in data["items"] if i.get("provider") == prov["provider"])
        if st in ("RECEIVING", "CONNECTED_NO_MESSAGES_YET") and age is not None and age > 180:
            label, kind = f"新闻采集中断 · 最近心跳 {hb}", "down"
        elif st == "RECEIVING" and delivered:
            label, kind = ("新闻源已接通 · REST 轮询（非推送）" if prov["provider"] in ("benzinga_rest", "massive_benzinga") else "新闻源已接通 · 实时推送"), "ok"
        elif st in ("RECEIVING", "CONNECTED_NO_MESSAGES_YET"):
            label, kind = "新闻源已连接，尚未观察到新消息", "idle"
        elif st in ("CONNECTING",):
            label, kind = "新闻源连接中", "idle"
        elif st == "STOPPED":
            label, kind = "新闻采集已停止", "down"
        elif st == "NOT_CONFIGURED":
            label, kind = "新闻源未配置", "off"
        else:
            label, kind = f"新闻源异常（{st}）", "down"
    data["connection"] = {"label": label, "kind": kind, "provider": prov["provider"] if prov else None}
    # Separate facts: online connection (above), latest receipt, and gap-fill completeness per REST channel.
    data["receipt"] = {"last_message_at": prov.get("last_message_at") if prov else None, "last_news_at": prov.get("last_news_at") if prov else None}
    try:
        from scripts.quant_news_v1.store import read_sync_state
        data["gap_fill"] = read_sync_state()
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        data["gap_fill"] = {"state": "UNREADABLE", "error_class": type(exc).__name__}
    # A live source is not a historical research panel: text research stays blocked until a panel qualifies.
    data["historical_news_panel_ready"] = False
    try:
        data["sources"] = json.loads((REPO_ROOT / "configs/quant_news_sources_v1.json").read_text(encoding="utf-8"))["providers"]
    except (OSError, ValueError, KeyError):
        data["sources"] = {}
    data["watchlist"] = sorted(watch)
    response = JsonResponse(data, json_dumps_params={"ensure_ascii": False})
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def x_stance_watch(request):
    """Owner-only switch for one watched author. It changes the platform's monitoring list only: nothing is followed
    or unfollowed on X, no provider is contacted, and a plan that is already frozen keeps the authors it has."""
    if not settings.PERSONAL_MODE or request.user.username != settings.PERSONAL_FEED_OWNER_USERNAME:
        raise Http404("X stance unavailable")
    import json
    import sys
    from datetime import datetime, timezone as dt_timezone
    from .personal_feed_cache import REPO_ROOT
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        body = json.loads(request.body.decode("utf-8") or "{}")
        if not isinstance(body, dict):
            raise ValueError
        watch_id, enabled = body.get("watch_id"), body.get("enabled")
        if not isinstance(watch_id, str) or not (1 <= len(watch_id) <= 40) or not isinstance(enabled, bool):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return _no_store(JsonResponse({"result": "REJECTED", "reason": "BAD_REQUEST"}, status=400))
    from scripts.x_stance_v1 import store as x_store, watchlist as x_watch
    try:
        db = x_store.connect()
        try:
            row = x_watch.set_enabled(db, watch_id, enabled, datetime.now(dt_timezone.utc))
        finally:
            db.close()
    except x_watch.WatchError as exc:
        return _no_store(JsonResponse({"result": "REJECTED", "reason": str(exc)}, status=400))
    except Exception as exc:  # noqa: BLE001 - a store that cannot be written is a status, not a page error
        return _no_store(JsonResponse({"result": "ERROR", "error_class": type(exc).__name__}, status=503))
    return _no_store(JsonResponse({"result": "UPDATED", "watch_id": row["watch_id"], "enabled": row["enabled"]}))
