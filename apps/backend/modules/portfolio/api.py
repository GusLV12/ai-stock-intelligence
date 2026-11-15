from decimal import Decimal

from django.db.models import Sum
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from modules.accounts.models import InvestmentAccount
from modules.instruments.models import Instrument

from .fiscal import sales_for_year, year_summary
from .models import LotDisposal, TaxLot, Transaction
from .services import LedgerError, get_position, record_buy, record_sell


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = [
            "id", "account", "instrument", "operation", "quantity", "price_per_unit", "fees",
            "currency", "executed_at", "settled_at", "broker_reference", "status", "created_at",
        ]  # fmt: skip
        read_only_fields = ["id", "status", "created_at", "settled_at"]
        extra_kwargs = {"currency": {"required": False}}

    def validate_account(self, account):
        if account.owner_id != self.context["request"].user.id:
            raise serializers.ValidationError("Cuenta no encontrada.")
        return account

    def validate(self, attrs):
        if attrs["quantity"] <= 0:
            raise serializers.ValidationError({"quantity": "Debe ser mayor que cero."})
        return attrs


class TransactionsView(APIView):
    def get(self, request):
        qs = Transaction.objects.filter(account__owner=request.user).order_by("-executed_at", "-id")
        if account := request.query_params.get("account"):
            qs = qs.filter(account_id=account)
        return Response(TransactionSerializer(qs, many=True, context={"request": request}).data)

    def post(self, request):
        ser = TransactionSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        fn = record_buy if d["operation"] == Transaction.Operation.BUY else record_sell
        try:
            tx = fn(
                account=d["account"],
                instrument=d["instrument"],
                quantity=d["quantity"],
                price=d["price_per_unit"],
                fees=d.get("fees", Decimal(0)),
                currency=d.get("currency"),
                executed_at=d["executed_at"],
                broker_reference=d.get("broker_reference", ""),
            )
        except LedgerError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(
            TransactionSerializer(tx, context={"request": request}).data, status=status.HTTP_201_CREATED
        )


def _positions_for(user, account_id=None, account_ids=None):
    """account_ids (opcional) restringe a un conjunto de cuentas; vacío = ninguna."""
    pairs = TaxLot.objects.filter(account__owner=user)
    if account_id:
        pairs = pairs.filter(account_id=account_id)
    if account_ids is not None:
        pairs = pairs.filter(account_id__in=list(account_ids))
    pairs = pairs.values_list("account_id", "instrument_id").distinct()
    accounts = {a.id: a for a in InvestmentAccount.objects.filter(owner=user)}
    instruments = {i.id: i for i in Instrument.objects.all()}
    rows = []
    for acc_id, ins_id in pairs:
        pos = get_position(accounts[acc_id], instruments[ins_id])
        rows.append(
            {
                "account": acc_id,
                "instrument": ins_id,
                "instrument_name": instruments[ins_id].name,
                "currency": instruments[ins_id].currency,
                "quantity": pos.quantity,
                "cost_basis": pos.cost_basis,
                "average_cost": pos.average_cost,
                "realized_pnl": pos.realized_pnl,
            }
        )
    return rows


class PositionsView(APIView):
    def get(self, request):
        rows = _positions_for(request.user, request.query_params.get("account"))
        return Response(_stringify(rows))


class SummaryView(APIView):
    """Resumen contable por moneda. Aún sin precios de mercado: no hay P&L no realizado."""

    def get(self, request):
        totals: dict[str, dict[str, Decimal]] = {}
        for r in _positions_for(request.user, request.query_params.get("account")):
            t = totals.setdefault(r["currency"], {"cost_basis": Decimal(0), "realized_pnl": Decimal(0)})
            t["cost_basis"] += r["cost_basis"]
            t["realized_pnl"] += r["realized_pnl"]
        return Response(
            {
                "by_currency": {c: {k: str(v) for k, v in t.items()} for c, t in totals.items()},
                "note": "Resultado contable en la moneda original; sin precios de mercado ni capa fiscal.",
            }
        )


class SalesView(APIView):
    """Historial de ventas con costo asignado y ganancia contable realizada."""

    def get(self, request):
        sales = Transaction.objects.filter(
            account__owner=request.user, operation=Transaction.Operation.SELL
        ).order_by("-executed_at", "-id")
        out = []
        for s in sales:
            agg = LotDisposal.objects.filter(sale=s).aggregate(c=Sum("cost_basis"), n=Sum("net_proceeds"))
            cost, net = agg["c"] or Decimal(0), agg["n"] or Decimal(0)
            out.append(
                {
                    "id": s.id,
                    "account": s.account_id,
                    "instrument": s.instrument_id,
                    "executed_at": s.executed_at,
                    "quantity": str(s.quantity),
                    "price_per_unit": str(s.price_per_unit),
                    "fees": str(s.fees),
                    "currency": s.currency,
                    "cost_basis": str(cost),
                    "net_proceeds": str(net),
                    "realized_pnl_book": str(net - cost),
                    "tax_status": "fiscal_pendiente",
                }
            )
        return Response(out)


class TaxYearSummaryView(APIView):
    def get(self, request, year):
        return Response(year_summary(request.user, year))


class TaxYearSalesView(APIView):
    def get(self, request, year):
        return Response(_stringify(sales_for_year(request.user, year)))


def _stringify(rows):
    return [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in rows]
