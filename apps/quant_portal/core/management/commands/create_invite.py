from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import transaction

from core.invite_files import write_private_invite
from core.models import Invitation


class Command(BaseCommand):
    help = "Create one email-bound invitation; its code is saved to an ignored private file."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--output", required=True, help="Path under app/var; must not already exist")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError("Invalid email") from exc
        days = options["days"]
        if not 1 <= days <= 30:
            raise CommandError("--days must be between 1 and 30")
        with transaction.atomic():
            _, code = Invitation.issue(email, timezone.now() + timedelta(days=days))
            path = write_private_invite(Path(options["output"]), code)
        self.stdout.write(f"Invite saved to private file: {path}")
