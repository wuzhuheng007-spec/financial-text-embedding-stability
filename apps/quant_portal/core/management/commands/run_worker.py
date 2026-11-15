"""Dedicated task process. It performs local housekeeping only in v1."""
import signal
import time

from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone

from core.models import WorkerHeartbeat


class Command(BaseCommand):
    help = "Run the isolated background worker (v1: session housekeeping, no provider requests)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--interval", type=int, default=30)

    def handle(self, *args, **options):
        interval = options["interval"]
        if not 5 <= interval <= 3600:
            raise CommandError("--interval must be 5..3600 seconds")
        self._stop = False
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)
        last_housekeeping = None
        while not self._stop:
            close_old_connections()
            now = timezone.now()
            WorkerHeartbeat.objects.update_or_create(
                name="portal_worker", defaults={"last_seen_at": now, "activity": "IDLE_NO_PROVIDER_ACCESS"},
            )
            if last_housekeeping is None or (now - last_housekeeping).total_seconds() >= 3600:
                Session.objects.filter(expire_date__lt=now).delete()
                last_housekeeping = now
            if options["once"]:
                break
            time.sleep(interval)
        self.stdout.write("worker stopped")

    def _request_stop(self, _signum, _frame):
        self._stop = True
