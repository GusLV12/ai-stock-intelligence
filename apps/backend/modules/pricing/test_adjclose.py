from datetime import date
from decimal import Decimal as D

import pytest

from .history import refresh_history
from .history_models import DailyClose
from .test_ohlcv import held, provider, response  # noqa: F401  (held es un fixture)


def con_ajuste(adj, closes=(11.0, 11.8)):
    resp = response([10.0, 11.0], [11.5, 12.0], [9.5, 10.5], list(closes), [1000, 2500])
    resp["chart"]["result"][0]["indicators"]["adjclose"] = [{"adjclose": adj}]
    return resp


def test_se_lee_el_cierre_ajustado_por_dividendos():
    out = provider(con_ajuste([10.5, 11.8])).fetch_history("AAA", "1mo")
    assert [b["adj_close"] for b in out["ohlcv"]] == [D("10.5000"), D("11.8000")]
    assert [c for _, c in out["bars"]] == [D("11.0000"), D("11.8000")]  # el cierre real no cambia


def test_sin_ajuste_del_proveedor_queda_vacio():
    out = provider(response([10.0], [11.5], [9.5], [11.0], [1000])).fetch_history("AAA", "1mo")
    assert out["ohlcv"][0]["adj_close"] is None


@pytest.mark.parametrize("adj", [[12.5, 11.8], [None, 11.8], [-1.0, 11.8], [0, 11.8]])
def test_ajuste_incoherente_se_descarta_solo_en_esa_barra(adj):
    out = provider(con_ajuste(adj)).fetch_history("AAA", "1mo")
    assert out["ohlcv"][0]["adj_close"] is None and out["ohlcv"][1]["adj_close"] == D("11.8000")


def test_se_guarda_y_se_corrige_hacia_atras_con_un_dividendo_nuevo(held):  # noqa: F811
    refresh_history(provider(con_ajuste([11.0, 11.8])), "1mo")
    assert [r.adj_close for r in DailyClose.objects.order_by("date")] == [D(11), D("11.8")]
    # el proveedor paga un dividendo: el ajuste histórico baja, el cierre real no se toca
    res = refresh_history(provider(con_ajuste([10.7, 11.8])), "1mo")
    rows = list(DailyClose.objects.order_by("date"))
    assert res["saved"] == {"AAA": 1} and rows[0].adj_close == D("10.7") and rows[0].close == D(11)
    assert refresh_history(provider(con_ajuste([10.7, 11.8])), "1mo")["saved"] == {}  # idempotente
    assert DailyClose.objects.filter(date=date(2026, 11, 10)).count() == 1
