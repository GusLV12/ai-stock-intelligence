# Liquidez: volumen, tamaño de posición y spread estimado

Implementación: `apps/backend/modules/technical/liquidity.py` (motor puro), `liquidity_api.py` (API) y la pestaña **Liquidez** de `/tecnico`.
Endpoints: `GET /api/v1/technical/liquidity?ticker=&window=&quantity=&participation=` y `GET /api/v1/technical/liquidity/portfolio`.

## Qué calcula
* **Volumen diario promedio (ADV)** en títulos y en dólares (media de la ventana, 20 días por defecto) y su mediana.
* **Nivel de liquidez** por volumen diario en dólares, con umbrales explícitos: ≥ 50 M alta, ≥ 5 M media, ≥ 0.5 M baja, menos muy baja.
* **Tamaño de la posición frente al volumen** (títulos / ADV), **días para operarla** suponiendo consumir como máximo 10 % del
  volumen diario (configurable) y una bandera: ≥ 1 % visible, ≥ 10 % alto.
* **Spread estimado** con Corwin-Schultz (2012) sobre máximos y mínimos, en una ventana de 120 días.
* **Iliquidez de Amihud** (|rendimiento| / volumen en dólares) como medida relativa.
* **Impacto aproximado** con la regla de la raíz cuadrada (σ diaria × √(posición / ADV), coeficiente 1).

## Límites que se declaran en pantalla y en la API
1. No hay libro de órdenes ni bid/ask: los datos son **cierres diarios** de un proveedor gratuito. El spread es una **estimación**.
2. Corwin-Schultz **no resuelve spreads pequeños**. En las emisoras muy líquidas de la cartera real el resultado es ≈ 0 y se muestra
   como «no distinguible de cero» (umbral 10 pb y error estándar). Con 20 días el estimador se inflaba por episodios volátiles
   (UBER: 51 pb con 20 días, 0 con 120/250), por eso se usa una ventana más larga que la del volumen y se promedian las estimaciones
   **sin truncar** (truncar cada una a cero sesga al alza; se comprobó con datos sintéticos de spread conocido).
3. El impacto es un orden de magnitud, no una predicción del costo de ejecución.
4. Con más de 10 % de días sin volumen en la ventana el resultado es «no evaluable»: no se rellenan huecos.
5. El volumen de Yahoo es consolidado del mercado de EE. UU.; no distingue plazas ni horarios.

## Resultado con la cartera real (2026-11-15)
22 de 22 posiciones evaluables, todas de liquidez alta; ninguna supera 0.001 % del volumen diario. Es una cartera pequeña frente a
emisoras grandes: la liquidez no es un riesgo hoy. Sirve para vigilar emisoras pequeñas si se añaden y para dimensionar órdenes.

## Pruebas
Datos sintéticos con propiedades conocidas (camino intradía browniano con spread inyectado): el estimador crece con el spread real,
es ≈ 0 sin él, y el impacto crece con el tamaño y la volatilidad. Además hay aislamiento por usuario, validación de entradas, auditoría
de rutas y una prueba e2e de la pestaña.
