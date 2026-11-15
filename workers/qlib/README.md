# Trabajadores del Quant Lab

Dos entornos Python aislados (no se mezclan con el de Django; sin base de datos ni red: leen un JSON y escriben otro).
La evaluación (IC, backtest, promoción) la hace siempre el backend, igual para todos los modelos.

| Entorno | Python | Contenido | Trabajador |
|---|---|---|---|
| `.venv` | 3.12 | LightGBM + numpy | `lgbm_worker.py` |
| `.venv311` | 3.11 | **Qlib real** (`pyqlib` 0.9.7) + LightGBM | `qlib_worker.py` |

`pyqlib` no instala en Python 3.12 con numpy 2.x; por eso Qlib vive en 3.11.

## Instalación

```bash
# LightGBM (3.12)
python -m venv workers/qlib/.venv
workers/qlib/.venv/Scripts/pip install -r workers/qlib/requirements.txt

# Qlib (3.11; con pyenv-win: pyenv install 3.11.9)
~/.pyenv/pyenv-win/pyenv-win/versions/3.11.9/python.exe -m venv workers/qlib/.venv311
workers/qlib/.venv311/Scripts/pip install -r workers/qlib/requirements-qlib.txt
```

Variables opcionales: `QUANT_WORKER_PYTHON` y `QUANT_QLIB_PYTHON` apuntan a otro intérprete.
Si un entorno falta, el modelo aparece como "no disponible" y el resto del laboratorio sigue funcionando.

## Qué hace `qlib_worker.py`

1. Escribe un proveedor de datos binario de Qlib con las velas (OHLCV ajustado por dividendos).
2. `Alpha158` calcula 158 factores solo con datos hasta cada fecha; se normalizan con la ventana de entrenamiento.
3. Etiqueta: retorno a `horizonte` sesiones con ejecución `delay` sesiones después (igual que el resto del laboratorio).
4. `LGBModel` de Qlib se entrena con `train`, se detiene con `valid` y puntúa desde `valid` hasta el final de los datos.
5. El registro de métricas de Qlib (MLflow) se escribe solo en una carpeta temporal que se borra al terminar.

Es lento (≈ 40 s por ajuste con 80 emisoras y 5 años; un experimento walk-forward de 4 bloques hace 5 ajustes), por eso
el modelo `qlib_lgbm` no va marcado por defecto.
