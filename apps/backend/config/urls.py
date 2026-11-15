from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import SimpleRouter

from modules.accounts.api import AccountViewSet
from modules.imports.api import ExportView, ImportConfirmView, ImportDetailView, ImportsView
from modules.instruments.api import InstrumentViewSet
from modules.portfolio.api import PositionsView, SalesView, SummaryView, TransactionsView

router = SimpleRouter(trailing_slash=False)
router.register("accounts", AccountViewSet, basename="account")
router.register("instruments", InstrumentViewSet, basename="instrument")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("modules.core.urls")),
    path("api/v1/portfolio/transactions", TransactionsView.as_view(), name="transactions"),
    path("api/v1/portfolio/positions", PositionsView.as_view(), name="positions"),
    path("api/v1/portfolio/summary", SummaryView.as_view(), name="summary"),
    path("api/v1/portfolio/sales", SalesView.as_view(), name="sales"),
    path("api/v1/imports", ImportsView.as_view(), name="imports"),
    path("api/v1/imports/<int:pk>", ImportDetailView.as_view(), name="import-detail"),
    path("api/v1/imports/<int:pk>/confirm", ImportConfirmView.as_view(), name="import-confirm"),
    path("api/v1/exports", ExportView.as_view(), name="exports"),
    path("api/v1/", include(router.urls)),
]
