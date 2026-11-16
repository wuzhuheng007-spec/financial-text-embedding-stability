"""Public-approved cache is separate from private research and provider stores."""
import hashlib
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone


symbol_validator = RegexValidator(r"^[A-Z0-9.]{1,16}$", "请输入不超过 16 位的大写证券代码。")


class Invitation(models.Model):
    email = models.EmailField(max_length=150)
    token_digest = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    @classmethod
    def issue(cls, email, expires_at, created_by=None):
        raw = secrets.token_urlsafe(32)
        invite = cls.objects.create(
            email=email.strip().lower(),
            token_digest=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            expires_at=expires_at,
            created_by=created_by,
        )
        return invite, raw

    @staticmethod
    def digest(raw):
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def usable(self):
        return self.consumed_at is None and self.expires_at > timezone.now()


class LoginThrottle(models.Model):
    key_digest = models.CharField(max_length=64, unique=True)
    window_start = models.DateTimeField()
    failed_count = models.PositiveSmallIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)


class WatchlistItem(models.Model):
    class AssetType(models.TextChoices):
        UNKNOWN = "UNKNOWN", "证券类型待核实"
        EQUITY = "EQUITY", "股票"
        ETF = "ETF", "ETF"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="watchlist_items")
    symbol = models.CharField(max_length=16, validators=[symbol_validator])
    display_name = models.CharField(max_length=100, blank=True)
    asset_type = models.CharField(max_length=8, choices=AssetType.choices, default=AssetType.UNKNOWN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "symbol"), name="unique_user_watch_symbol")]
        ordering = ("symbol",)

    def clean(self):
        self.symbol = self.symbol.strip().upper()
        if self.display_name:
            self.display_name = self.display_name.strip()


class UserPreferences(models.Model):
    class DisplayLanguage(models.TextChoices):
        ZH = "zh", "中文"
        EN = "en", "English"
    class DisplayTimezone(models.TextChoices):
        SHANGHAI = "Asia/Shanghai", "Asia/Shanghai"
        NEW_YORK = "America/New_York", "America/New_York"
        UTC = "UTC", "UTC"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="preferences")
    display_timezone = models.CharField(max_length=32, choices=DisplayTimezone.choices, default=DisplayTimezone.SHANGHAI)
    display_language = models.CharField(max_length=2, choices=DisplayLanguage.choices, default=DisplayLanguage.ZH)


class PublicFactCard(models.Model):
    """Only an explicit rights-reviewed row can appear in shared pages."""
    class FactKind(models.TextChoices):
        ACTUAL = "ACTUAL", "Historical reported result"
        GUIDANCE = "GUIDANCE", "Future guidance at historical release"

    class SourceClass(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Unreviewed source"
        OFFICIAL = "OFFICIAL_PUBLIC", "Reviewed official public source"
        DEMO = "DEMO", "Clearly labeled demonstration"
        PROVIDER = "PROVIDER_RESTRICTED", "Provider restricted"

    issuer = models.CharField(max_length=120)
    fiscal_period = models.CharField(max_length=120)
    fact_kind = models.CharField(max_length=12, choices=FactKind.choices, default=FactKind.ACTUAL)
    fact_topic = models.CharField(max_length=40, blank=True)
    metric = models.CharField(max_length=120)
    value_text = models.CharField(max_length=120)
    range_lower_text = models.CharField(max_length=120, blank=True)
    range_upper_text = models.CharField(max_length=120, blank=True)
    range_derivation = models.CharField(max_length=120, blank=True)
    range_status = models.CharField(max_length=40, blank=True)
    unit_text = models.CharField(max_length=60, blank=True)
    currency = models.CharField(max_length=20, blank=True)
    basis_text = models.CharField(max_length=60, blank=True)
    basis_note = models.CharField(max_length=250, blank=True)
    source_url = models.URLField()
    as_of = models.DateTimeField(null=True, blank=True)
    source_date = models.DateField(null=True, blank=True)
    source_time_precision = models.CharField(max_length=20, default="UNKNOWN")
    source_event_id = models.CharField(max_length=120, blank=True)
    candidate_id = models.CharField(max_length=40, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True)
    source_manifest_sha256 = models.CharField(max_length=64, blank=True)
    evidence_locator = models.TextField(blank=True)
    evidence_state = models.CharField(max_length=32, default="SOURCE_REVIEW_PENDING")
    source_review_status = models.CharField(max_length=40, blank=True)
    source_class = models.CharField(max_length=20, choices=SourceClass.choices, default=SourceClass.UNKNOWN)
    visibility_scope = models.CharField(max_length=20, default="LOCAL_ONLY")
    rights_basis = models.CharField(max_length=250, blank=True)
    display_approved = models.BooleanField(default=False)
    cached_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("candidate_id",), condition=~models.Q(candidate_id=""),
                name="unique_nonempty_fact_candidate",
            )
        ]

    @property
    def range_derivation_display(self):
        labels = {
            "central_billion * (1 +/- percentage/100)": "原文中心值 × (1 ± 原文百分比 / 100)",
            "central_billion +/- delta_million/1000": "原文中心值 ± 原文百万美元容差 / 1000",
        }
        return labels.get(self.range_derivation, self.range_derivation)

    def clean(self):
        if self.display_approved and not self.rights_basis.strip():
            raise ValidationError("Approved display requires a recorded rights basis.")
        if self.display_approved and self.source_class not in (self.SourceClass.OFFICIAL, self.SourceClass.DEMO):
            raise ValidationError("Only reviewed official or labeled demo cards can be displayed.")
        if self.display_approved and self.visibility_scope != "SHARED_APPROVED":
            raise ValidationError("Shared display requires a separately approved scope.")
        if self.source_url and not self.source_url.lower().startswith("https://"):
            raise ValidationError("Fact-card source links must use HTTPS.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class PublicMarketSnapshot(models.Model):
    class SourceClass(models.TextChoices):
        RESTRICTED = "PROVIDER_RESTRICTED", "Provider restricted"
        LICENSED = "LICENSED_PUBLIC", "Public display separately licensed"

    class DelayStatus(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Delay unknown"
        DELAYED = "DELAYED", "Delayed"
        REALTIME = "REALTIME", "Real-time verified"

    symbol = models.CharField(max_length=16, validators=[symbol_validator])
    provider = models.CharField(max_length=32)
    price_text = models.CharField(max_length=60)
    currency = models.CharField(max_length=12)
    provider_time = models.DateTimeField()
    received_at = models.DateTimeField()
    delay_status = models.CharField(max_length=12, choices=DelayStatus.choices, default=DelayStatus.UNKNOWN)
    market_state = models.CharField(max_length=32, default="UNKNOWN")
    source_class = models.CharField(max_length=20, choices=SourceClass.choices, default=SourceClass.RESTRICTED)
    rights_basis = models.CharField(max_length=250, blank=True)
    display_approved = models.BooleanField(default=False)
    cached_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.display_approved and not self.rights_basis.strip():
            raise ValidationError("Approved display requires a recorded rights basis.")
        if self.display_approved and self.source_class != self.SourceClass.LICENSED:
            raise ValidationError("Public market data must have a separate display license.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class ProviderState(models.Model):
    provider = models.CharField(max_length=32, unique=True)
    state = models.CharField(max_length=40, default="NOT_CONNECTED")
    last_success_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=40, primary_key=True)
    last_seen_at = models.DateTimeField()
    activity = models.CharField(max_length=80, default="IDLE_NO_PROVIDER_ACCESS")
