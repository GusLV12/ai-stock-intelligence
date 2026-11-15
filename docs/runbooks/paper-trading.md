# Paper trading (Etapa 12)

Cuenta **virtual** de práctica para medir si una idea (por ejemplo, el ranking de un experimento del Quant Lab) habría funcionado
con ejecución realista. **No hay dinero real, ni conexión con ningún broker, ni forma de enviar una orden real**: el módulo
`modules/paper` no importa red, clientes de brokers ni el libro contable (`portfolio`, `imports`), y una prueba lo verifica leyendo
el código. Tu portafolio, lotes y capa fiscal reales nunca se tocan.

## Reglas del simulador (todas explícitas)

* Solo posiciones **largas**: sin ventas en corto ni apalancamiento; el efectivo nunca queda negativo.
* **Orden a mercado**: se ejecuta al último precio conocido (el más reciente entre un precio guardado y el último cierre diario),
  con su proveedor y antigüedad, **± deslizamiento adverso** (compra más cara, venta más barata; 5 pb por defecto) y comisión
  configurable. Si el precio tiene más de 5 días, la orden se **rechaza** y queda guardada con el motivo.
* **Orden limitada**: espera y se reevalúa con `process_paper_orders` (o el botón *Reprocesar pendientes*). Con cierres diarios no se
  ven toques intradía, así que es una aproximación **optimista** de cuándo habría ejecutado; vence a los 30 días. Nunca se ejecuta
  peor que el límite.
* Tope por emisora (35 % del valor de la cuenta por defecto) y moneda igual a la de la cuenta (USD).
* Utilidad realizada con **FIFO** y comisiones incluidas. Todo con `Decimal`.
* El estado se reconstruye siempre desde las órdenes ejecutadas (única fuente de verdad). Las órdenes decididas y el diario son
  **inmutables**; las cuentas no se borran.
* Cada ejecución guarda precio de referencia, procedencia y **discrepancia (deslizamiento adverso en pb)**: así se ve cuánto se
  habría perdido en fricción respecto de lo que supone un backtest.

## Uso

* Web: `/paper` (crear cuenta, orden simulada, propuesta desde un experimento, posiciones, órdenes, curva de capital, métricas y
  diario de decisiones).
* La **propuesta desde un experimento** convierte el ranking más reciente en órdenes *propuestas* equiponderadas (con banda del 2 %
  para no operar por diferencias insignificantes y respetando el tope por emisora). Las propuestas no se ejecutan solas, y si el
  experimento no fue promovido lo dice (úsalo como ensayo, sin esperar ventaja).
* `python manage.py process_paper_orders` reevalúa las órdenes limitadas de todas las cuentas con los precios guardados
  (corre antes `refresh_market_data` o `refresh_price_history`).
* API: `/api/v1/paper/accounts…` (ver `modules/paper/api.py`). Todo requiere sesión y está aislado por usuario.

## Métricas y sus límites

Capital diario, rendimiento, caída máxima, operaciones cerradas, tasa de acierto, factor de beneficio y deslizamiento adverso medio.
El Sharpe **no se calcula** con menos de 30 observaciones diarias (sería ruido), y la tasa de acierto se marca como no concluyente
con menos de 30 operaciones cerradas. Una cuenta de práctica de semanas no prueba nada: sirve para ver el flujo, la fricción real y
tu propia disciplina, no para validar una estrategia.
