from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import SimpleRouter

from modules.accounts.api import AccountViewSet
from modules.assistant.api import ChatView
from modules.calculators.api import CagrView, TargetBuyPriceView
from modules.fundamentals.api import ImportView as FundamentalsImportView
from modules.fundamentals.api import InstrumentFinancialsView
from modules.fundamentals.screener_api import RunDetailView, RunListView, RunScreenView
from modules.imports.api import (
    ExportView,
    ImportConfirmView,
    ImportDetailView,
    ImportsView,
    PositionsImportView,
    SnapshotOpeningView,
    SnapshotReconciliationView,
)
from modules.instruments.api import InstrumentViewSet
from modules.intelligence.api import ExposureView, PoliciesView
from modules.intelligence.rebalance_api import RebalancePreviewView
from modules.intelligence.risk_api import RiskView
from modules.intelligence.shocks_api import PresetsView, ScenarioView
from modules.intelligence.thesis_api import FollowUpView, ThesesView, ThesisDetailView
from modules.portfolio.api import (
    PositionsView,
    SalesView,
    SummaryView,
    TaxYearSalesView,
    TaxYearSummaryView,
    TransactionsView,
)
from modules.portfolio.dashboard import DashboardView
from modules.pricing.api import FxView, PricesView
from modules.technical.alerts_api import AlertsView, CancelAlertView, EvaluateAlertsView
from modules.technical.api import AnalyzeView, ScanView, SymbolsView, TradePlanView
from modules.technical.intraday_api import IntradayView
from modules.valuations.api import DcfView, FairValueDetailView, FairValueView, ReverseDcfView

router = SimpleRouter(trailing_slash=False)
router.register("accounts", AccountViewSet, basename="account")
router.register("instruments", InstrumentViewSet, basename="instrument")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("modules.core.urls")),
    path("api/v1/portfolio/transactions", TransactionsView.as_view(), name="transactions"),
    path("api/v1/portfolio/positions", PositionsView.as_view(), name="positions"),
    path("api/v1/portfolio/dashboard", DashboardView.as_view(), name="dashboard"),
    path("api/v1/portfolio/summary", SummaryView.as_view(), name="summary"),
    path("api/v1/portfolio/sales", SalesView.as_view(), name="sales"),
    path("api/v1/tax/years/<int:year>/summary", TaxYearSummaryView.as_view(), name="tax-summary"),
    path("api/v1/tax/years/<int:year>/sales", TaxYearSalesView.as_view(), name="tax-sales"),
    path("api/v1/portfolio/policies", PoliciesView.as_view(), name="policies"),
    path("api/v1/portfolio/rebalance-preview", RebalancePreviewView.as_view(), name="rebalance-preview"),
    path("api/v1/portfolio/scenarios/presets", PresetsView.as_view(), name="scenario-presets"),
    path("api/v1/portfolio/scenarios", ScenarioView.as_view(), name="scenarios"),
    path("api/v1/portfolio/risk", RiskView.as_view(), name="portfolio-risk"),
    path("api/v1/portfolio/theses", ThesesView.as_view(), name="theses"),
    path("api/v1/portfolio/theses/<int:pk>", ThesisDetailView.as_view(), name="thesis-detail"),
    path("api/v1/portfolio/theses/<int:pk>/follow-ups", FollowUpView.as_view(), name="thesis-follow-ups"),
    path("api/v1/technical/symbols", SymbolsView.as_view(), name="technical-symbols"),
    path("api/v1/technical/analyze", AnalyzeView.as_view(), name="technical-analyze"),
    path("api/v1/technical/intraday", IntradayView.as_view(), name="technical-intraday"),
    path("api/v1/technical/alerts", AlertsView.as_view(), name="technical-alerts"),
    path("api/v1/technical/alerts/evaluate", EvaluateAlertsView.as_view(), name="technical-alerts-evaluate"),
    path("api/v1/technical/alerts/<int:pk>/cancel", CancelAlertView.as_view(), name="technical-alert-cancel"),
    path("api/v1/technical/scan", ScanView.as_view(), name="technical-scan"),
    path("api/v1/technical/trade-plan", TradePlanView.as_view(), name="technical-trade-plan"),
    path("api/v1/portfolio/exposure", ExposureView.as_view(), name="exposure"),
    path("api/v1/screeners/fair-value", RunScreenView.as_view(), name="screener-run"),
    path("api/v1/screeners/runs", RunListView.as_view(), name="screener-runs"),
    path("api/v1/screeners/runs/<int:pk>", RunDetailView.as_view(), name="screener-run-detail"),
    path("api/v1/fundamentals/import", FundamentalsImportView.as_view(), name="fundamentals-import"),
    path("api/v1/fundamentals/<int:pk>", InstrumentFinancialsView.as_view(), name="fundamentals-detail"),
    path("api/v1/calculators/cagr", CagrView.as_view(), name="calc-cagr"),
    path("api/v1/calculators/target-buy-price", TargetBuyPriceView.as_view(), name="calc-target-price"),
    path("api/v1/valuations/fair-value", FairValueView.as_view(), name="fair-value"),
    path("api/v1/valuations/dcf", DcfView.as_view(), name="valuation-dcf"),
    path("api/v1/valuations/reverse-dcf", ReverseDcfView.as_view(), name="valuation-reverse-dcf"),
    path("api/v1/valuations/<int:pk>", FairValueDetailView.as_view(), name="valuation-detail"),
    path("api/v1/assistant/chat", ChatView.as_view(), name="assistant-chat"),
    path("api/v1/market/prices", PricesView.as_view(), name="market-prices"),
    path("api/v1/market/fx", FxView.as_view(), name="market-fx"),
    path("api/v1/imports/positions", PositionsImportView.as_view(), name="import-positions"),
    path("api/v1/snapshots/<int:pk>/reconciliation", SnapshotReconciliationView.as_view(), name="snapshot-rec"),
    path("api/v1/snapshots/<int:pk>/opening-balances", SnapshotOpeningView.as_view(), name="snapshot-opening"),
    path("api/v1/imports", ImportsView.as_view(), name="imports"),
    path("api/v1/imports/<int:pk>", ImportDetailView.as_view(), name="import-detail"),
    path("api/v1/imports/<int:pk>/confirm", ImportConfirmView.as_view(), name="import-confirm"),
    path("api/v1/exports", ExportView.as_view(), name="exports"),
    path("api/v1/", include(router.urls)),
]
