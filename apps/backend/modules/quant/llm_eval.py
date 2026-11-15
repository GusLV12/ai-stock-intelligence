"""Evaluación de si un LLM local puede ser el motor de RD-Agent(Q): ¿produce salida estructurada y código de factores
correcto y seguro con la fiabilidad suficiente? (plan 10.6: "primero se evaluará si Qwen3 local soporta la generación de
código y salida estructurada con fiabilidad suficiente").

Se le piden factores de series de precios (cada uno con una referencia exacta) y se mide:
  - JSON válido con la forma pedida,
  - código seguro: una sola función `factor(closes)` que pasa una lista blanca de sintaxis (sin imports, atributos,
    lambdas, while, exec…); el código del modelo NUNCA se ejecuta si no pasa esa lista,
  - código correcto: coincide con la referencia en todos los casos de prueba.
El veredicto sale de umbrales fijos. Esto no ejecuta RD-Agent: solo decide si tiene sentido intentarlo con ese modelo."""

import ast
import json
import math
import random
import sys

TOLERANCE = 1e-6
THRESHOLDS = {"json_valid": 0.95, "code_safe": 0.95, "correct": 0.75}
ALLOWED_CALLS = {
    "len",
    "sum",
    "min",
    "max",
    "abs",
    "sqrt",
    "range",
    "float",
    "int",
    "sorted",
    "zip",
    "enumerate",
}
ALLOWED_NODES = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign, ast.AugAssign, ast.For, ast.If, ast.IfExp,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Name, ast.Constant, ast.Subscript, ast.Slice, ast.List,
    ast.Tuple, ast.ListComp, ast.GeneratorExp, ast.comprehension, ast.Load, ast.Store, ast.Expr,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.And, ast.Or, ast.Not,
    ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
)  # fmt: skip
MAX_CONSTANT = 1_000
MAX_LOOP_DEPTH = 2  # bucles anidados: 1000 x 1000 como máximo
MAX_POW = 4
MAX_RANGE = 1_000
MAX_STEPS = 300_000  # eventos de línea por llamada a factor()


class UnsafeCode(ValueError):
    pass


def _small_constant(node) -> bool:
    return (
        isinstance(node, ast.Constant) and isinstance(node.value, int | float) and abs(node.value) <= MAX_POW
    )


def _loop_depth(node, depth: int = 0) -> int:
    inc = 1 if isinstance(node, ast.For | ast.ListComp | ast.GeneratorExp) else 0
    return max([depth + inc] + [_loop_depth(ch, depth + inc) for ch in ast.iter_child_nodes(node)])


def _limited_range(*args):
    r = range(*args)
    if len(r) > MAX_RANGE:
        raise UnsafeCode("range demasiado grande.")
    return r


def _with_step_limit(fn):
    """Envuelve `factor` para que aborte si ejecuta demasiadas líneas (bucles largos)."""

    def run(closes):
        steps = 0

        def tracer(frame, event, arg):
            nonlocal steps
            steps += 1
            if steps > MAX_STEPS:
                raise UnsafeCode("El código excedió el límite de pasos.")
            return tracer

        old = sys.gettrace()
        sys.settrace(tracer)
        try:
            return fn(closes)
        finally:
            sys.settrace(old)

    return run


def compile_factor(code: str):
    """Valida con lista blanca y devuelve la función `factor`. Lanza UnsafeCode si algo no está permitido."""
    if not isinstance(code, str) or len(code) > 4000:
        raise UnsafeCode("Código ausente o demasiado largo.")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise UnsafeCode(f"Sintaxis inválida: {exc.msg}.") from None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef) or tree.body[0].name != "factor":
        raise UnsafeCode("Debe ser exactamente una función llamada factor.")
    fn = tree.body[0]
    if (
        [a.arg for a in fn.args.args] != ["closes"]
        or fn.args.defaults
        or fn.args.vararg
        or fn.args.kwarg
        or fn.decorator_list
    ):
        raise UnsafeCode("La firma debe ser factor(closes).")
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise UnsafeCode(f"Sintaxis no permitida: {type(node).__name__}.")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise UnsafeCode("Nombres con doble guion bajo no permitidos.")
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name) and node.func.id in ALLOWED_CALLS
        ):
            raise UnsafeCode("Solo se permiten llamadas a funciones de la lista blanca.")
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int | float)
            and abs(node.value) > MAX_CONSTANT
        ):
            raise UnsafeCode("Constante demasiado grande.")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            raise UnsafeCode("No se permiten textos.")
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) and not _small_constant(node.right):
            raise UnsafeCode("Las potencias deben tener exponente constante pequeño.")
    if _loop_depth(fn) > MAX_LOOP_DEPTH:
        raise UnsafeCode("Demasiados bucles anidados.")
    for node in ast.walk(tree):  # [0] * n y similares: bombas de memoria
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mult | ast.Add)
            and (
                isinstance(node.left, ast.List | ast.Tuple | ast.ListComp)
                or isinstance(node.right, ast.List | ast.Tuple | ast.ListComp)
            )
        ):
            raise UnsafeCode("No se permite multiplicar ni concatenar listas.")
    safe_builtins = {
        n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n)
        for n in ALLOWED_CALLS - {"sqrt"}
    }
    safe_builtins["range"] = _limited_range
    env = {"__builtins__": safe_builtins, "sqrt": math.sqrt}
    exec(compile(tree, "<factor>", "exec"), env)  # noqa: S102  # nosec B102 - el AST ya pasó la lista blanca (sin imports, atributos ni nombres peligrosos)
    return _with_step_limit(env["factor"])


# ---------------------------------------------------------------------------------------------- tareas y referencias
def _mean(x):
    return sum(x) / len(x)


def _rets(c, n):
    return [c[i] / c[i - 1] - 1 for i in range(len(c) - n, len(c))]


TASKS = [
    ("momentum_10", "retorno de los últimos 10 periodos: closes[-1] / closes[-11] - 1", lambda c: c[-1] / c[-11] - 1),
    ("sma_gap_20", "distancia relativa del último cierre a su media simple de 20 cierres: closes[-1] / media(últimos 20) - 1", lambda c: c[-1] / _mean(c[-20:]) - 1),
    ("vol_20", "desviación estándar MUESTRAL (divide entre n-1) de los 20 últimos retornos simples diarios", lambda c: math.sqrt(sum((r - _mean(_rets(c, 20))) ** 2 for r in _rets(c, 20)) / 19)),
    ("drawdown_60", "caída del último cierre respecto al máximo de los últimos 60 cierres: closes[-1] / max(últimos 60) - 1", lambda c: c[-1] / max(c[-60:]) - 1),
    ("up_share_20", "fracción de los últimos 20 retornos simples diarios que fueron positivos (mayores que 0)", lambda c: sum(1 for r in _rets(c, 20) if r > 0) / 20),
    ("max_gain_30", "mayor retorno simple diario de los últimos 30 periodos", lambda c: max(_rets(c, 30))),
    ("range_pos_20", "posición del último cierre dentro del rango de los últimos 20 cierres: (último - mínimo) / (máximo - mínimo)", lambda c: (c[-1] - min(c[-20:])) / (max(c[-20:]) - min(c[-20:]))),
    ("zscore_20", "puntaje z del último cierre: (closes[-1] - media de los últimos 20) / desviación estándar POBLACIONAL (divide entre n) de esos 20", lambda c: (c[-1] - _mean(c[-20:])) / math.sqrt(sum((x - _mean(c[-20:])) ** 2 for x in c[-20:]) / 20)),
]  # fmt: skip

SYSTEM = (
    "Eres un asistente de investigación cuantitativa. Responde SOLO con un objeto JSON de la forma "
    '{"name": "<nombre_corto>", "code": "<código Python>"}. El código debe ser exactamente una función '
    "`def factor(closes):` que recibe una lista de cierres (floats, del más antiguo al más reciente, al menos 120) y devuelve un float. "
    "No uses import, atributos con punto, lambdas, while, ni texto. Puedes usar len, sum, min, max, abs, sqrt, range, float, sorted, zip y enumerate."
)


def fixtures(seed: int = 0, cases: int = 4, length: int = 130) -> list[list[float]]:
    rng = random.Random(seed)  # nosec B311 - datos de prueba reproducibles, no criptografía
    out = []
    for _ in range(cases):
        p, series = 100.0, []
        for _ in range(length):
            p *= 1 + rng.gauss(0.0003, 0.012)
            series.append(round(p, 4))
        out.append(series)
    return out


def score_answer(task_id: str, raw: str, data: list[list[float]]) -> dict:
    """Puntúa una respuesta: JSON válido, código seguro y correcto. Nunca ejecuta código que no pasó la lista blanca."""
    ref = next(f for t, _, f in TASKS if t == task_id)
    res = {"json_valid": False, "code_safe": False, "correct": False, "error": None}
    try:
        obj = json.loads(raw)
        if not (
            isinstance(obj, dict) and isinstance(obj.get("name"), str) and isinstance(obj.get("code"), str)
        ):
            raise TypeError("Forma inesperada.")
        res["json_valid"] = True
        fn = compile_factor(obj["code"])
        res["code_safe"] = True
        ok = True
        for closes in data:
            got = fn(list(closes))
            want = ref(closes)
            if (
                not isinstance(got, int | float)
                or math.isnan(got)
                or abs(got - want) > TOLERANCE * max(1.0, abs(want))
            ):
                ok = False
                break
        res["correct"] = ok
    except (ValueError, TypeError, UnsafeCode) as exc:
        res["error"] = str(exc)[:120]
    except Exception as exc:  # noqa: BLE001 - cualquier error al ejecutar el código del modelo cuenta como incorrecto
        res["error"] = f"Error al ejecutar: {type(exc).__name__}"
    return res


def evaluate(generate, repeats: int = 3, seed: int = 0) -> dict:
    """`generate(system, prompt) -> str` es el LLM (inyectado para poder probarlo). Devuelve tasas y veredicto."""
    data = fixtures(seed)
    rows = []
    for task_id, spec, _ in TASKS:
        for k in range(repeats):
            try:
                raw = generate(SYSTEM, f"Escribe el factor «{task_id}»: {spec}.")
            except Exception as exc:  # noqa: BLE001 - cualquier fallo del modelo cuenta como respuesta fallida
                rows.append(
                    {
                        "task": task_id,
                        "run": k,
                        "json_valid": False,
                        "code_safe": False,
                        "correct": False,
                        "error": str(exc)[:120],
                    }
                )
                continue
            rows.append({"task": task_id, "run": k, **score_answer(task_id, raw, data)})
    n = len(rows)
    rates = {m: sum(r[m] for r in rows) / n for m in THRESHOLDS}
    ok = all(rates[m] >= t for m, t in THRESHOLDS.items())
    return {
        "runs": n, "rates": rates, "thresholds": THRESHOLDS, "rows": rows,
        "verdict": "APTO_PARA_INTENTAR_RD_AGENT" if ok else "NO_APTO",
        "by_task": {t: sum(r["correct"] for r in rows if r["task"] == t) / repeats for t, _, _ in TASKS},
    }  # fmt: skip
