# Quant Lab (Etapa 11)

Laboratorio de **investigación**: mide si una señal habría tenido capacidad predictiva con datos pasados. No es una
recomendación de inversión, no genera órdenes y un resultado positivo solo significa "seguir observando".

## Qué hace

1. **Panel**: factores por emisora y fecha calculados solo con datos hasta esa fecha (`ret_5/20/60`, `vol_20`,
   distancias a medias, `dd_60`, `rsi_14`), normalizados por rango dentro de cada fecha.
2. **Retorno total**: se usa el **cierre ajustado por dividendos** de Yahoo cuando está completo para la emisora; si falta
   alguna barra se usa el cierre real de toda la emisora (no se mezclan bases) y el resultado lo declara.
   `DailyClose.adj_close` se corrige hacia atrás con cada dividendo nuevo; `close` (el cierre real) nunca se modifica.
3. **Etiqueta**: retorno a 5 o 20 sesiones con ejecución una sesión después de la señal
   (`close[t+1+h] / close[t+1] − 1`).
4. **Validación walk-forward** (4 bloques por defecto): la zona de prueba (todo lo posterior al 40 % de la historia) se
   parte en bloques contiguos; en cada uno se entrena solo con lo anterior (ventana creciente), se valida con el tramo
   final de esa historia y se purgan `delay + horizonte` fechas entre segmentos. `folds=1` da una sola partición.
5. **Modelos**: baselines (momentum 20/60, reversión 5, baja volatilidad, aleatorio de control), regresión ridge
   (λ elegido en validación), LightGBM y **Qlib real** (Alpha158 + LGBModel de Qlib, lento y opt-in).
6. **Métricas**: IC y Rank IC por fecha, ICIR, t ingenuo y **t con muestra efectiva** (n/horizonte), % de fechas con IC
   positivo y Rank IC por bloque.
7. **Backtest**: top-K long-only sobre todo el periodo fuera de muestra, rebalanceo cada `horizonte` sesiones, costos por
   lado (comisión + medio spread + deslizamiento, 5 pb por defecto), límite de 5 % del volumen diario en dinero, y dos
   referencias: universo en partes iguales con el mismo rebalanceo/costos y compra-y-mantén.
8. **Promoción**: criterio fijo en `modules/quant/promotion.py` (ver abajo).
9. **Reproducibilidad**: cada experimento guarda configuración, `as_of`, huella de los datos y huella del resultado;
   "Volver a correr y comparar" repite todo con los mismos datos y avisa si los datos históricos cambiaron (por ejemplo,
   un dividendo nuevo reajusta el historial).

## Criterio de promoción

Veredictos: `PROMOVER_A_SEGUIMIENTO`, `NO_PROMOVIDO`, `DATOS_INSUFICIENTES`.

| Criterio | Umbral por defecto |
|---|---|
| Fechas de prueba | ≥ 60 |
| Emisoras en el universo | ≥ 20 |
| Rank IC medio en prueba | ≥ 0.02 |
| t con muestra efectiva | ≥ 2.0, endurecido con Bonferroni según los intentos previos sobre los mismos datos |
| Fechas con IC positivo | ≥ 55 % |
| Rank IC en validación | > 0 |
| Supera al mejor baseline (Rank IC prueba) | sí |
| Periodos de prueba independientes (walk-forward) | ≥ 3 |
| Periodos con Rank IC positivo | ≥ 60 % |
| Exceso neto de costos vs universo igual | > 0 |

Si faltan fechas o emisoras, el veredicto es `DATOS_INSUFICIENTES` y no se interpreta nada más. "Promover" solo
significa seguimiento (paper trading, más datos), nunca operar. El candidato se elige **solo con la validación**; la
prueba no retroalimenta ninguna decisión. Cada experimento adicional sobre los mismos datos cuenta como un intento y sube
el umbral de t; los experimentos son inmutables, así que el historial de intentos no se puede maquillar.

## Cómo usarlo

```bash
# 1) Universo de investigación (70 acciones grandes de EE. UU.) + 5 años de historial (Yahoo, no oficial).
#    Solo se envían símbolos públicos. Los instrumentos nuevos son `research_only`: no aparecen en tu catálogo,
#    screener ni precios.
python manage.py load_research_universe --range 5y
python manage.py refresh_price_history --range 5y        # el historial de tus propias emisoras

# 2) Un experimento por comando (o desde /quant en la web)
python manage.py run_quant_experiment --user <usuario> --horizon 20 --top-k 10
python manage.py run_quant_experiment --user <usuario> --models momentum_20,ridge,lightgbm,qlib_lgbm   # incluye Qlib
```

Cada instrumento exige ≥ 71 cierres. Un experimento walk-forward con 80 emisoras y 5 años tarda ≈ 1 min sin Qlib y
≈ 3 min con Qlib.

## Resultado con datos reales (2026-11-15)

80 emisoras (70 del universo + tu cartera), 5 años, horizonte 20, 4 bloques walk-forward, costos de 5 pb por lado:
**ningún modelo muestra señal fuera de muestra**. Rank IC en prueba: momentum 20 +0.013, momentum 60 −0.020,
reversión +0.001, baja volatilidad −0.058, aleatorio +0.004, ridge +0.019, LightGBM +0.035, Qlib Alpha158 +0.019; ningún
t ajustado supera 1.3. Veredicto: `NO_PROMOVIDO`. Es un resultado esperable y creíble: el control aleatorio queda en 0 y los
factores sencillos no bastan en acciones grandes y líquidas.

## Qlib, LightGBM y entornos

Ver `workers/qlib/README.md`. LightGBM corre en `.venv` (Python 3.12) y **Qlib real (`pyqlib` 0.9.7) en `.venv311`
(Python 3.11)**, porque `pyqlib` no instala en 3.12 con numpy 2.x. Ambos son trabajadores aislados (sin base de datos ni
red); la evaluación la hace siempre el backend, igual para todos los modelos. Si un entorno falta, el modelo aparece
"no disponible" y el resto funciona.

## RD-Agent(Q): decisión

**No se incorporó, con evidencia.** El plan lo condiciona a evaluar si el LLM local genera código y salida estructurada
con fiabilidad suficiente. `python manage.py eval_quant_llm --model <modelo>` pide 8 factores con referencia exacta
(3 repeticiones) y mide JSON válido, código seguro (lista blanca de sintaxis, límite de pasos y de `range`; el código que
no la pasa nunca se ejecuta) y código correcto. Umbrales: 95 % / 95 % / 75 %.

`qwen3:1.7b` (el único modelo instalado en esta PC de 7.2 GB): JSON 100 %, seguro 100 %, **correcto 25 %** (acierta
rendimiento a 10 periodos y caída desde el máximo; falla media, volatilidad, z-score, etc., y escapa doble los saltos de
línea, que se reparan y se declaran) → `NO_APTO`. Además RD-Agent necesita Linux/Docker aislado, sin credenciales ni
documentos reales montados. Pendiente: repetir la evaluación con `qwen3:4b` / `qwen3:8b` en la ROG Xbox Ally X (24 GB);
solo si pasa los umbrales tiene sentido intentar RD-Agent(Q) en un contenedor.

## Limitaciones que siguen vigentes (declaradas también en pantalla)

* **Sesgo de supervivencia**: el universo son empresas grandes de HOY. Corregirlo exige la composición histórica del
  índice fecha por fecha, que es un dato licenciado; con datos gratuitos no se puede. Se incluyeron a propósito varias
  emisoras que han rendido mal (INTC, PFE, BA, T, VZ, MMM, DIS), lo que mitiga pero no elimina el sesgo. Por eso un
  resultado positivo solo significa "seguir observando". El universo está versionado (`us-large-caps-v1`).
* Yahoo es una fuente gratuita no oficial: el ajuste por dividendos y las velas pueden tener errores o cambiar.
* Walk-forward evalúa varios periodos, pero todos pertenecen al mismo tramo de historia (un solo régimen de mercado).
* Cálculos estadísticos en `float` (no contables); sin impuestos ni tipo de cambio a MXN.
* Los factores propios son una línea base sencilla; Alpha158 (Qlib) es el catálogo estándar, tampoco validado en este mercado.
