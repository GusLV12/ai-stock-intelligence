import json

import pytest

from . import llm_eval
from .llm_eval import UnsafeCode, compile_factor, evaluate, fixtures, score_answer

CORRECT = {
    "momentum_10": "def factor(closes):\n    return closes[-1] / closes[-11] - 1",
    "sma_gap_20": "def factor(closes):\n    return closes[-1] / (sum(closes[-20:]) / 20) - 1",
    "vol_20": (
        "def factor(closes):\n    r = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]\n"
        "    m = sum(r) / 20\n    return sqrt(sum((x - m) ** 2 for x in r) / 19)"
    ),
    "drawdown_60": "def factor(closes):\n    return closes[-1] / max(closes[-60:]) - 1",
    "up_share_20": (
        "def factor(closes):\n    r = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]\n"
        "    return sum(1 for x in r if x > 0) / 20"
    ),
    "max_gain_30": "def factor(closes):\n    return max(closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 30, len(closes)))",
    "range_pos_20": (
        "def factor(closes):\n    w = closes[-20:]\n    return (closes[-1] - min(w)) / (max(w) - min(w))"
    ),
    "zscore_20": (
        "def factor(closes):\n    w = closes[-20:]\n    m = sum(w) / 20\n    return (closes[-1] - m) / sqrt(sum((x - m) ** 2 for x in w) / 20)"
    ),
}


def answer(code, name="f"):
    return json.dumps({"name": name, "code": code})


def test_las_referencias_correctas_pasan_todas_las_tareas():
    data = fixtures()
    for task, code in CORRECT.items():
        r = score_answer(task, answer(code), data)
        assert r == {
            "json_valid": True,
            "code_safe": True,
            "correct": True,
            "error": None,
            "repaired": False,
        }, (task, r)


def test_un_generador_perfecto_es_apto():
    def perfecto(system, prompt):
        task = prompt.split("«")[1].split("»")[0]
        return answer(CORRECT[task])

    res = evaluate(perfecto, repeats=2)
    assert res["verdict"] == "APTO_PARA_INTENTAR_RD_AGENT" and res["rates"] == {
        "json_valid": 1.0,
        "code_safe": 1.0,
        "correct": 1.0,
    }


def test_un_generador_que_confunde_muestral_con_poblacional_no_es_correcto():
    malo = CORRECT["zscore_20"].replace("/ 20)", "/ 19)")
    r = score_answer("zscore_20", answer(malo), fixtures())
    assert r["json_valid"] and r["code_safe"] and r["correct"] is False


@pytest.mark.parametrize(
    "code",
    [
        "import os\ndef factor(closes):\n    return 1.0",
        "def factor(closes):\n    import os\n    return 1.0",
        "def factor(closes):\n    return closes.__class__",
        "def factor(closes):\n    return closes.pop()",  # atributo con punto
        "def factor(closes):\n    return __import__('os').getcwd()",
        "def factor(closes):\n    return eval('1')",
        "def factor(closes):\n    return exec('x=1')",
        "def factor(closes):\n    return open('x')",
        "def factor(closes):\n    while True:\n        pass",
        "def factor(closes):\n    return (lambda: 1)()",
        "def factor(closes):\n    return 'texto'",
        "def factor(closes):\n    return sum(range(10**9))",
        "def factor(closes):\n    return 1.0\nprint('x')",
        "def otra(closes):\n    return 1.0",
        "def factor(a, b):\n    return 1.0",
        "@decorador\ndef factor(closes):\n    return 1.0",
        "def factor(closes):\n    return [x for x in closes if x.real][0]",
        "def factor(closes) return 1",
        "",
    ],
)
def test_codigo_peligroso_o_mal_formado_se_rechaza_sin_ejecutarse(code):
    with pytest.raises(UnsafeCode):
        compile_factor(code)


def test_el_codigo_inseguro_nunca_se_ejecuta(tmp_path):
    marca = tmp_path / "ejecutado.txt"
    codigo = f"import pathlib\npathlib.Path(r'{marca}').write_text('x')\ndef factor(closes):\n    return 1.0"
    r = score_answer("momentum_10", answer(codigo), fixtures())
    assert r["json_valid"] and not r["code_safe"] and not marca.exists()


@pytest.mark.parametrize("raw", ["no es json", "[]", '{"name": 1, "code": "x"}', '{"name": "a"}', ""])
def test_json_invalido_o_con_forma_inesperada(raw):
    r = score_answer("momentum_10", raw, fixtures())
    assert r["json_valid"] is False and r["correct"] is False


def test_error_en_ejecucion_se_contabiliza_como_incorrecto_sin_romper():
    r = score_answer(
        "momentum_10",
        answer("def factor(closes):\n    return closes[-1] / (closes[-1] - closes[-1])"),
        fixtures(),
    )
    assert r["code_safe"] and r["correct"] is False and "ZeroDivisionError" in r["error"]


def test_el_modelo_caido_no_rompe_la_evaluacion():
    def caido(system, prompt):
        raise ConnectionError("sin conexión")

    res = evaluate(caido, repeats=1)
    assert (
        res["verdict"] == "NO_APTO"
        and res["rates"]["json_valid"] == 0.0
        and res["runs"] == len(llm_eval.TASKS)
    )


@pytest.mark.parametrize(
    "code",
    [
        "def factor(closes):\n    return sum(range(999 * 999 * 999))",  # range gigante con constantes chicas
        "def factor(closes):\n    return len([0] * 999 * 999 * 999)",
        "def factor(closes):\n    t = 0.0\n    for i in range(900):\n        for j in range(900):\n            t += 1\n    return t",  # 810 000 pasos
        "def factor(closes):\n    return closes[-1] ** 3 ** 3",
    ],
)
def test_bombas_de_tiempo_y_memoria_se_cortan(code):
    try:
        fn = compile_factor(code)
    except UnsafeCode:
        return  # rechazado en la validación estática
    with pytest.raises(UnsafeCode):
        fn(list(range(1, 130)))  # o cortado al ejecutarse


def test_saltos_de_linea_doblemente_escapados_se_reparan_y_se_declaran():
    doble = json.dumps({"name": "m", "code": CORRECT["momentum_10"].replace(chr(10), chr(92) + "n")})
    r = score_answer("momentum_10", doble, fixtures())
    assert r["correct"] is True and r["repaired"] is True
