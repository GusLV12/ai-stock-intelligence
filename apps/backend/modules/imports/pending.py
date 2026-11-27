"""Cambios observados entre reportes; no son asientos de venta."""

from collections import Counter
from decimal import Decimal
from itertools import pairwise

from .models import PositionSnapshot
from .snapshots import QTY_TOLERANCE


def pending_sales(account):
    snapshots = list(
        PositionSnapshot.objects.filter(account=account).prefetch_related("lines").order_by("as_of", "id")
    )
    counts = Counter(s.as_of for s in snapshots)
    pending = []
    for previous, current in pairwise(snapshots):
        # No adivinar el orden de dos archivos con la misma fecha.
        if counts[previous.as_of] != 1 or counts[current.as_of] != 1:
            continue
        before = {row.ticker: row.quantity for row in previous.lines.all()}
        after = {row.ticker: row.quantity for row in current.lines.all()}
        for ticker, old in sorted(before.items()):
            new = after.get(ticker, Decimal(0))
            difference = old - new
            if difference <= QTY_TOLERANCE:
                continue
            pending.append(
                {
                    "id": f"{previous.id}:{current.id}:{ticker}",
                    "ticker": ticker,
                    "quantity": str(difference),
                    "reported_before": str(old),
                    "reported_after": str(new),
                    "previous_snapshot": previous.id,
                    "current_snapshot": current.id,
                    "previous_as_of": previous.as_of,
                    "current_as_of": current.as_of,
                    "kind": "total" if new == 0 else "parcial",
                    "status": "pendiente_revision",
                }
            )
    return pending
