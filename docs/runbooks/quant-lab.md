# Quant Lab (Etapa 11)

Laboratorio de **investigación**: mide si una señal habría tenido capacidad predictiva con datos pasados. No es una
recomendación de inversión, no genera órdenes y un resultado positivo solo significa "seguir observando".

## Qué hace

1. **Panel**: factores por emisora y fecha calculados solo con cierres hasta esa fecha (`ret_5/20/60`, `vol_20`,
   distancias a medias, `dd_60`, `rsi_14`), normalizados por rango dentro de cada fecha.
2. **Etiqueta**: retorno a 5 o 20 sesiones con ejecución una sesión después de la señal
   (`close[t+1+h] / close[t+1] − 1`).
3. **Partición temporal con purga**: entrenamiento < validación < prueba; se descartan `delay + horizonte` fechas entre
   segmentos para que las etiquetas no se solapen.
4. **Modelos**: baselines (momentum 20/60, reversión 5, baja volatilidad, aleatorio de control), regresión ridge
   (λ elegido en validación) y LightGBM (entorno aislado).
5. **Métricas**: IC y Rank IC por fecha, ICIR, t ingenuo y **t con muestra efectiva** (n/horizonte), % de fechas con IC
   positivo y estabilidad por bloques.
6. **Backtest**: top-K long-only, rebalanceo cada `horizonte` sesiones, costos por lado (comisión + medio spread +
   deslizamiento, 5 pb por defecto), límite de 5 % del volumen diario en dinero, y dos referencias: universo en
   partes iguales con el mismo rebalanceo/costos y compra-y-mantén.
7. **Promoción**: criterio fijo en `modules/quant/promotion.py` (ver abajo).
8. **Reproducibilidad**: cada experimento guarda configuración, `as_of`, huella de los datos y huella del resultado;
   "Volver a correr y comparar" repite todo con los mismos datos y avisa si los datos históricos cambiaron.

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
| Exceso neto de costos vs universo igual | > 0 |

Si faltan fechas o emisoras, el veredicto es `DATOS_INSUFICIENTES` y no se interpreta nada más. "Promover" solo
significa seguimiento (paper trading, más datos), nunca operar.

El candidato se elige **solo con la validación** entre los modelos que aprenden (ridge, LightGBM). La prueba no
retroalimenta ninguna decisión. Cada experimento adicional sobre los mismos datos cuenta como un intento y sube el
umbral de t (pruebas múltiples); los experimentos son inmutables, por lo que el historial no se puede maquillar.

## Cómo usarlo

```bash
# 1) Historial largo de las emisoras (Yahoo, no oficial). Cuantas más emisoras y años, más útil.
python manage.py refresh_price_history --range 5y

# 2) Un experimento por comando (o desde /quant en la web)
python manage.py run_quant_experiment --user <usuario> --horizon 20
```

Cada experimento exige ≥ 71 cierres por emisora. Para que el veredicto no sea `DATOS_INSUFICIENTES` hacen falta al
menos 20 emisoras con historial y ≥ 60 fechas de prueba (en la práctica, varios años).

## LightGBM y Qlib

* LightGBM corre en su propio entorno (`workers/qlib/.venv`), sin base de datos ni red: lee un JSON y escribe otro.
  La evaluación (IC, backtest, promoción) la hace siempre el backend, igual para todos los modelos.
* Instalación: `python -m venv workers/qlib/.venv` y `workers/qlib/.venv/Scripts/pip install -r workers/qlib/requirements.txt`.
  Si no está instalado, el modelo aparece como "no disponible" y el resto sigue funcionando
  (`QUANT_WORKER_PYTHON` permite apuntar a otro intérprete).
* **Qlib (`pyqlib`) no se instaló**: no resolvió dependencias con Python 3.12 y numpy 2.x en este equipo. El pipeline
  sigue la metodología de Qlib (factores → etiqueta → partición temporal → modelo → IC/Rank IC → backtest) con código
  propio. Si se quisiera Qlib real, iría en un entorno Python 3.10/3.11 aparte, como prevé el plan.

## Limitaciones (declaradas también en pantalla)

* Universo = instrumentos con historial guardado hoy: **sesgo de supervivencia sin corregir**.
* Cierres ajustados por splits, sin dividendos reinvertidos; proveedor gratuito no oficial.
* Una sola partición temporal: un buen resultado puede deberse al periodo y no a la señal.
* Cálculos estadísticos en `float` (no contables); sin impuestos ni tipo de cambio a MXN.
* Los factores son una línea base sencilla, no un catálogo validado (no es Alpha158).

## Pendiente de la Etapa 11

* **RD-Agent(Q)**: no se incorporó. El plan lo condiciona a consolidar Qlib y a evaluar si Qwen3 local genera código y
  salida estructurada con fiabilidad; además requiere Linux/Docker aislado y sin credenciales ni documentos reales.
* Universo histórico con cambios de composición, factores adicionales (fundamentales con `published_at`), validación
  walk-forward con varios periodos y modelos de ranking específicos.
