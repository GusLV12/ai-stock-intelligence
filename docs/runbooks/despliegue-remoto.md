# Despliegue remoto privado (Etapa 12)

Cómo publicar el sistema con acceso controlado, qué está verificado y qué **no**. Léelo completo antes de abrir nada a internet.

## Arquitectura

```
internet ──► Caddy (80/443, HTTPS automático) ──► /api/*  ──► backend (uvicorn, Django)  ──► db (PostgreSQL, red interna)
                                              └─► resto  ──► web (Next.js)
internet ──► Caddy (mcp.tudominio.com)       ──► mcp (Streamable HTTP + OAuth, solo lectura)
backup (cada 24 h) ──► volumen `backups` (cifrado)      ollama (perfil `inference`, red interna)
```

* Solo Caddy publica puertos. PostgreSQL vive en una red `internal: true` (sin salida a internet).
* Contenedores sin privilegios: usuario no root, `cap_drop: ALL`, `no-new-privileges`, sistema de archivos de solo lectura
  (salvo PostgreSQL, Caddy y Ollama, que necesitan escribir), límites de memoria.
* El panel `/admin` de Django **no existe** en producción (404) salvo que definas `DJANGO_ADMIN_URL` con una ruta propia; además
  Caddy lo bloquea en el dominio público.
* Sin DEBUG, con cookies seguras, HSTS de un año, redirección a HTTPS y `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS` obligatorios:
  Django **se niega a arrancar** con `*` en hosts o con orígenes `http://` (probado).

## Primer despliegue

1. Servidor Linux con Docker y un dominio con dos registros DNS (`asi.tudominio.com` y, si usarás MCP, `mcp.tudominio.com`).
2. `cp infra/.env.prod.example infra/.env.prod` y rellénalo. Genera los secretos:
   `python -c "import secrets; print(secrets.token_urlsafe(64))"` (clave Django y contraseña de BD) y
   `python manage.py generate_backup_key` (clave de respaldos; **guárdala también en tu gestor de contraseñas**).
   Ese archivo no se sube al repositorio ni se pega en chats.
3. `docker compose -f infra/compose.prod.yaml --env-file infra/.env.prod up -d --build`
4. Crea tu usuario: `docker compose ... exec backend python manage.py createsuperuser` (contraseña interactiva).
5. Verifica: `https://asi.tudominio.com/api/v1/ready` debe responder `{"status":"ready"…}`.
6. **Haz la prueba de restauración** (abajo) antes de cargar datos reales.

## Respaldos y restauración

* El servicio `backup` corre `manage.py backup_db` cada 24 h (`BACKUP_INTERVAL_SECONDS`), conserva `BACKUP_KEEP` archivos y
  **verifica cada respaldo** al crearlo. Formato `.asibak`: cifrado y autenticado (Fernet); un archivo alterado o con otra clave
  falla de forma explícita.
* Un volumen no es un respaldo: **copia el volumen `backups` fuera del servidor** (otro disco, otro proveedor). Sin copia externa,
  un fallo del servidor se lleva los datos y los respaldos juntos.
* PostgreSQL: respaldo con `pg_dump -Fc`. La verificación automática es **superficial** (lee el índice del archivo y comprueba que
  cada tabla del manifiesto esté). La prueba profunda es manual:

  ```bash
  docker compose -f infra/compose.prod.yaml --env-file infra/.env.prod exec backup \
      python manage.py restore_db /backups/asi-backup-XXXX.asibak --pg-database asi_restore_test
  ```

  Crea una base **nueva** (nunca toca la activa), restaura y falla si algo no carga. Compara conteos con la app y bórrala después.
* Reemplazar la base real es siempre manual y con la app detenida: restaura a una base nueva, revisa, y cambia `DB_NAME`.
* SQLite (desarrollo): `backup_db`, `verify_backup` y `restore_db --target` hacen la restauración completa de prueba y comparan filas
  por tabla con el manifiesto (probado, incluida una base real en archivo).

## MCP remoto (OAuth)

Este sistema es solo el **servidor de recursos**. Necesitas un proveedor de identidad propio (Keycloak, Authentik, Auth0, Entra…)
que autentique y emita los tokens; **no se incluye ni se simula uno**.

1. Configura en el proveedor un recurso/audiencia `https://mcp.tudominio.com/mcp` y los scopes `portfolio:read`,
   `transactions:read`, `calculators:run`.
2. Define `MCP_OAUTH_ISSUER` (y `MCP_OAUTH_JWKS_URL` si no es `<issuer>/.well-known/jwks.json`) y arranca el perfil:
   `docker compose ... --profile mcp up -d`.
3. Vincula tu identidad (el `sub` de tu usuario en el proveedor) a cuentas y scopes concretos:
   `manage.py link_mcp_oauth --user tuusuario --issuer https://idp... --subject <sub> --name "Claude remoto" --scopes portfolio:read --accounts GBM`.
4. Revoca cuando quieras con `manage.py revoke_mcp_key <id>`: surte efecto en la siguiente llamada.

Qué se verifica de cada token: firma con las llaves públicas del emisor (**solo algoritmos asimétricos**; se rechazan `none` y HS*),
`iss` exacto, `aud` igual a **este** recurso (un token emitido para otra API no sirve: sin "token passthrough"), `exp`/`nbf`, `sub`.
Los permisos efectivos son la **intersección** de los scopes del token y los de la credencial vinculada; las cuentas siempre son
las de la credencial. Además: protección contra DNS rebinding (`MCP_ALLOWED_HOSTS`), límite de 60 llamadas/min y auditoría.
El MCP **no puede** comprar, vender ni mover fondos: no existe ninguna herramienta de escritura.

## Inferencia separada

`--profile inference` levanta Ollama en la red interna. Para que el backend lo use hay que declarar `OLLAMA_URL=http://ollama:11434`
y `OLLAMA_ALLOW_REMOTE=true` a conciencia: tus datos financieros se enviarán a ese servicio (tuyo, sin salida a internet salvo para
descargar modelos). Sin ese permiso explícito el cliente se niega.

## Observabilidad

* `/api/v1/health` (viveza) y `/api/v1/ready` (base de datos y migraciones) sin sesión y sin datos internos; el healthcheck del
  contenedor usa `ready`.
* Registros JSON por salida estándar con `X-Request-ID` en cada petición y **secretos redactados** (contraseñas, tokens, claves,
  cookies, cabeceras de autorización, hashes largos). `docker compose logs -f backend`.
* No hay todavía métricas, trazas ni alertas externas (ver pendientes).

## Auditoría de seguridad (qué se comprobó)

| Control | Resultado |
|---|---|
| Todas las rutas `/api/v1/*` sin sesión, con GET/POST/PUT/PATCH/DELETE | Solo `health`, `ready`, `auth/csrf` y `auth/login` responden; el resto 401/403/405 (prueba automática que falla si se agrega una ruta sin protección). La auditoría encontró que `/ready` aceptaba POST y se corrigió. |
| Ninguna ruta ejecuta órdenes reales | El módulo `paper` no importa red, brokers ni el libro contable real (prueba que lee el código); el MCP es de solo lectura. |
| `manage.py check --deploy --fail-level WARNING` con configuración de producción | Sin advertencias (la precarga HSTS W021 se silencia a propósito: es casi irreversible y la decide el dueño del dominio). |
| Dependencias | `pip-audit` sobre `requirements.txt`: sin vulnerabilidades conocidas; `npm audit --omit=dev`: 0. |
| `bandit` sobre todo el código | 0 hallazgos sin justificar (las excepciones llevan `nosec` con motivo). |
| Secretos | Ninguna clave en el repositorio; `.dockerignore` excluye `.env`, bases, XLSX/PDF y respaldos del contexto de construcción. |
| Respaldos | Cifrados y autenticados; clave, archivo alterado, truncado o ajeno fallan con mensaje claro; nunca pisan la base en uso. |

## Lo que NO está verificado (decláralo antes de confiar)

* **Verificado el 2026-11-15 en una PC local con Docker real** (PostgreSQL 17, dominio `localhost`, secretos desechables): las
  imágenes `asi-backend` y `asi-web` se construyen; las 41 migraciones aplican en PostgreSQL; backend sano (`ready`); el servicio
  de respaldos genera `.asibak` verificados; `restore_db --pg-database` restauró a una base nueva con las **39 tablas con las mismas
  filas** y el mismo hash de las transacciones; Caddy sirve HTTPS (certificado interno), redirige HTTP→HTTPS, aplica las cabeceras
  y `/admin` responde 404. La corrida encontró y corrigió tres fallos reales (variables `mcp` exigidas sin su perfil, `pywin32` en
  requirements para Linux, y `/backups` sin permisos para el usuario sin privilegios).
* **No probado todavía**: el perfil `mcp` y el `inference` (Ollama), la importación de datos reales dentro del contenedor, ni la
  carga con 3.7 GB de RAM para Docker en esta PC (justa). Primer paso al desplegar de verdad: repetir el recorrido con tu dominio.
* **PostgreSQL**: ya se probó contra un servidor real, pero con pocos datos (217 filas). Repite la restauración profunda con tus datos
  reales antes de confiar en ella.
* **Let's Encrypt (emisión pública) y el proveedor de identidad** no se probaron contra servicios reales (el protocolo MCP/OAuth sí se probó
  contra la aplicación ASGI real con tokens firmados de prueba).
* La CSP permite scripts en línea (necesario para hidratar Next.js); endurecerla con nonces es un paso posterior.
* `uvicorn` confía en las cabeceras `X-Forwarded-*` de cualquier origen (`--forwarded-allow-ips "*"`) porque solo Caddy alcanza el
  backend por la red interna; si expones el puerto 8000 deja de ser seguro: no lo publiques.
* Los secretos viven en un archivo `.env.prod` en el servidor, no en un gestor de secretos; protege el archivo (`chmod 600`) y el servidor.
* Sin fail2ban/WAF, sin rotación de claves automática, sin métricas ni alertas, sin análisis de imágenes (Trivy/Grype), sin fijar
  las imágenes por digest. Todo eso es razonable antes de exponer datos reales a internet.
* El modelo de amenazas asume un único usuario dueño del sistema: no hay roles, 2FA ni bloqueo por intentos más allá del límite de 5/min en el login.
