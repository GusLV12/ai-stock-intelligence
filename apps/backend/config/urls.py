from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import SimpleRouter

from modules.accounts.api import AccountViewSet
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
    path("api/v1/", include(router.urls)),
]
