from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction
from django.utils import timezone

from modules.technical.models import PortfolioHistoryRun
from modules.technical.portfolio_refresh import create_run, execute_run

NY = ZoneInfo("America/New_York")


class Command(BaseCommand):
    help = "Actualiza cierres técnicos de cartera, sin modificar Quant Lab."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", type=int)
        parser.add_argument("--scheduled", action="store_true")

    def handle(self, *args, **options):
        if options["run_id"]:
            execute_run(options["run_id"])
            return
        if not options["scheduled"]:
            self.stderr.write("Indica --scheduled o --run-id.")
            return
        now = timezone.now().astimezone(NY)
        target = now.date() if now.hour >= 18 else now.date() - timedelta(days=1)
        for user in get_user_model().objects.filter(investment_accounts__transactions__isnull=False).distinct():
            scheduled = PortfolioHistoryRun.objects.filter(user=user, schedule_date=target).first()
            if scheduled and scheduled.status == "running" and scheduled.updated_at < timezone.now() - timedelta(minutes=30):
                PortfolioHistoryRun.objects.filter(pk=scheduled.pk, status="running").update(status="interrupted")
                scheduled.refresh_from_db()
            if scheduled and scheduled.status == "interrupted":
                try:
                    with transaction.atomic():
                        resumed = PortfolioHistoryRun.objects.filter(pk=scheduled.pk, status="interrupted").update(
                            status="running", results=[], total=0, finished_at=None, started_at=timezone.now(), updated_at=timezone.now()
                        )
                except IntegrityError:
                    resumed = 0
                if resumed:
                    execute_run(scheduled.pk)
                continue
            if scheduled:
                continue
            run, created = create_run(user, "scheduled", target)
            if created:
                execute_run(run.pk)
