"""Motor de paper trading: funciones puras con Decimal (sin base de datos, sin red, sin brokers).

Reglas del simulador (todas explícitas, para que un resultado simulado no se confunda con uno real):
- Solo posiciones largas: no hay ventas en corto ni apalancamiento (el efectivo nunca queda negativo).
- Una orden a mercado se ejecuta al último precio conocido ± deslizamiento adverso (compra más caro, venta más barato)
  más comisión. El precio de referencia trae proveedor y antigüedad; si es más viejo que el límite, la orden se rechaza.
- Una orden limitada se evalúa con precios posteriores; con cierres diarios no se pueden ver toques intradía, así que
  es una aproximación optimista de cuándo habría ejecutado y se declara.
- La utilidad realizada usa FIFO (primeras entradas, primeras salidas) y descuenta comisiones.
Nada de este módulo toca el libro contable real ni envía órdenes a ningún broker."""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

BPS = Decimal(10_000)
PRICE_Q = Decimal("0.0001")
MAX_QTY = Decimal(1000000000)


class PaperError(ValueError):
    pass


@dataclass(frozen=True)
class Rules:
    slippage_bps: Decimal = Decimal(5)  # deslizamiento + medio spread, en contra del operador
    commission_bps: Decimal = Decimal(0)  # sobre el valor operado
    commission_min: Decimal = Decimal(0)  # mínimo por orden
    max_price_age_days: int = 5
    max_position_pct: Decimal = Decimal("0.35")  # tope por emisora sobre el valor total de la cuenta

    @classmethod
    def from_config(cls, cfg: dict) -> "Rules":
        def d(key, default):
            return Decimal(str(cfg.get(key, default)))

        return cls(
            d("slippage_bps", 5),
            d("commission_bps", 0),
            d("commission_min", 0),
            int(cfg.get("max_price_age_days", 5)),
            d("max_position_pct", "0.35"),
        )


def fill_price(reference: Decimal, side: str, slippage_bps: Decimal) -> Decimal:
    """Compra: más cara; venta: más barata. Siempre en contra del operador."""
    if reference <= 0:
        raise PaperError("El precio de referencia debe ser positivo.")
    k = slippage_bps / BPS
    px = reference * (1 + k if side == "BUY" else 1 - k)
    return px.quantize(PRICE_Q, rounding=ROUND_HALF_UP)


def commission(notional: Decimal, rules: Rules) -> Decimal:
    if notional <= 0:
        return Decimal(0)
    return max(notional * rules.commission_bps / BPS, rules.commission_min).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )


def check_quantity(qty: Decimal, scale: int) -> Decimal:
    if not isinstance(qty, Decimal) or not qty.is_finite() or qty <= 0 or qty > MAX_QTY:
        raise PaperError("La cantidad debe ser un número positivo.")
    if qty != qty.quantize(Decimal(1).scaleb(-scale)):
        raise PaperError(f"Esa emisora admite como máximo {scale} decimales de cantidad.")
    return qty


@dataclass(frozen=True)
class Fill:
    order_id: int
    instrument: int
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal
    at: object  # datetime u orden cronológico; se usa solo para ordenar


@dataclass
class State:
    cash: Decimal
    lots: dict = field(default_factory=dict)  # instrumento -> [[cantidad, precio, comisión por título]]
    realized: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    closed: list = field(default_factory=list)

    def quantity(self, instrument: int) -> Decimal:
        return sum((lot[0] for lot in self.lots.get(instrument, [])), Decimal(0))

    def cost_basis(self, instrument: int) -> Decimal:
        return sum((lot[0] * (lot[1] + lot[2]) for lot in self.lots.get(instrument, [])), Decimal(0))

    def positions(self) -> dict:
        return {i: self.quantity(i) for i, lots in self.lots.items() if lots and self.quantity(i) > 0}


def apply_fill(state: State, f: Fill) -> None:
    """Aplica un fill al estado. Lanza PaperError si lo dejaría inválido (efectivo negativo o venta sin posición)."""
    gross = f.quantity * f.price
    if f.side == "BUY":
        cost = gross + f.fee
        if cost > state.cash:
            raise PaperError("Efectivo insuficiente.")
        state.cash -= cost
        state.lots.setdefault(f.instrument, []).append([f.quantity, f.price, f.fee / f.quantity])
    elif f.side == "SELL":
        if f.quantity > state.quantity(f.instrument):
            raise PaperError("No puedes vender más de lo que tienes (no hay ventas en corto).")
        state.cash += gross - f.fee
        remaining, sell_fee_per_unit = f.quantity, f.fee / f.quantity
        lots = state.lots[f.instrument]
        while remaining > 0:
            lot = lots[0]
            take = min(lot[0], remaining)
            pnl = take * (f.price - lot[1]) - take * (lot[2] + sell_fee_per_unit)
            state.realized += pnl
            state.closed.append(
                {
                    "order_id": f.order_id,
                    "instrument": f.instrument,
                    "quantity": take,
                    "buy_price": lot[1],
                    "sell_price": f.price,
                    "pnl": pnl,
                }
            )
            lot[0] -= take
            remaining -= take
            if lot[0] == 0:
                lots.pop(0)
    else:
        raise PaperError("El lado debe ser BUY o SELL.")
    state.fees += f.fee


def replay(initial_cash: Decimal, fills: list[Fill]) -> State:
    """Reconstruye la cuenta desde cero con los fills en orden cronológico (única fuente de verdad)."""
    state = State(cash=initial_cash)
    for f in sorted(fills, key=lambda x: (x.at, x.order_id)):
        apply_fill(state, f)
    return state


def equity(state: State, marks: dict) -> Decimal:
    """Efectivo + posiciones a precio de mercado. `marks`: instrumento -> precio. Una posición sin precio se valúa a su costo."""
    total = state.cash
    for inst, qty in state.positions().items():
        px = marks.get(inst)
        total += qty * px if px is not None else state.cost_basis(inst)
    return total


def validate_buy(
    state: State, instrument: int, qty: Decimal, price: Decimal, fee: Decimal, marks: dict, rules: Rules
) -> None:
    cost = qty * price + fee
    if cost > state.cash:
        raise PaperError("Efectivo insuficiente para esta compra (incluye comisión).")
    total = equity(state, marks)
    after = (state.quantity(instrument) + qty) * price
    if total > 0 and after / total > rules.max_position_pct:
        raise PaperError(f"La posición superaría el {rules.max_position_pct:.0%} del valor de la cuenta.")


def validate_sell(state: State, instrument: int, qty: Decimal) -> None:
    if qty > state.quantity(instrument):
        raise PaperError("No puedes vender más de lo que tienes (no hay ventas en corto).")
