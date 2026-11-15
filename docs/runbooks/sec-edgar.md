# Estados financieros desde SEC EDGAR

La SEC (Comisión de Valores de EE. UU.) publica gratis los informes oficiales de cada emisora. `ingest_sec_financials` descarga los
**anuales (10-K)** y los guarda como `FinancialYear` (fuente `SEC_EDGAR`), que alimentan el Fair Value y el screener.

## Preparación (una vez)
La SEC exige identificar a quien consulta con un nombre y un correo en cada petición. Defínelo en tu `.env` local (ignorado por git):

    SEC_CONTACT="Nombre Apellido correo@dominio"

Ese dato **se envía a los servidores de la SEC**; usa uno que aceptes compartir. Sin la variable no se hace ninguna consulta.

## Uso
```bash
cd apps/backend
python manage.py ingest_sec_financials --held                    # acciones/ADR con operaciones registradas
python manage.py ingest_sec_financials --tickers AMD,MU --years 8
python manage.py ingest_sec_financials --held --dry-run          # descarga y valida, no guarda
```
Es idempotente: repetirlo no crea versiones; un valor distinto crea una versión nueva y conserva la anterior. Cada emisora se importa
por separado: si una falla, las demás se guardan y el comando termina con error indicando cuáles.

## Qué hace y qué NO hace
* Usa el valor **tal como se publicó originalmente** y la fecha de presentación como `published_at` (sin sesgo de anticipación en
  backtests). Las **reexpresiones posteriores no se capturan**: para eso usa la importación por CSV.
* **EBITDA** = utilidad operativa + depreciación y amortización, y **deuda total** = deuda de largo plazo + porción corriente +
  préstamos de corto plazo + papel comercial (sin arrendamientos). Son derivados; si falta un componente, queda vacío.
* Solo **10-K con US-GAAP**. Emisoras extranjeras con 20-F/IFRS (TSM, SONY, NU...) y fondos (ETF, metales: GLDM, PSLV) se **omiten**
  con un aviso; para ellas, importa por CSV.
* Cortesía de tasa: ≥ 0.15 s entre consultas (la SEC permite 10 por segundo). Solo se conecta por HTTPS a `www.sec.gov` y
  `data.sec.gov`.
* El año fiscal es el año del cierre del periodo (cierres del 1 al 7 de enero de años de 52/53 semanas cuentan al año anterior).

## Estado de la verificación
Probado con respuestas **simuladas** (16 pruebas: mapeo, valor original frente a comparativo reexpresado, trimestres excluidos,
derivados, 20-F, límites de red, idempotencia, aislamiento de fallos). **Falta la primera prueba en vivo** con tu `SEC_CONTACT`:
ejecútala con `--dry-run` y compara un par de cifras con el 10-K de la emisora antes de confiar en el resultado.
