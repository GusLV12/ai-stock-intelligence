import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PortfolioHistoryRun
from .portfolio_refresh import create_run


def serialize(run):
    if run is None:
        return None
    return {"id": run.pk, "status": run.status, "source": run.source, "started_at": run.started_at,
            "finished_at": run.finished_at, "total": run.total, "results": run.results}


class PortfolioHistoryRefreshView(APIView):
    def get(self, request):
        return Response(serialize(PortfolioHistoryRun.objects.filter(user=request.user).order_by("-started_at").first()))

    def post(self, request):
        run, created = create_run(request.user)
        if not created:
            return Response({"detail": "Ya hay una actualización en curso.", "run": serialize(run)}, status=409)
        try:
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
            subprocess.Popen([sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "refresh_technical_portfolio", "--run-id", str(run.pk)],
                             cwd=settings.BASE_DIR, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, **kwargs)  # nosec B603
        except OSError:
            run.status = "failed"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at", "updated_at"])
            return Response({"detail": "No se pudo iniciar la actualización."}, status=503)
        return Response(serialize(run), status=202)
