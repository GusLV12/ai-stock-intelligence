# Instrucciones para Codex

Lee `CLAUDE.md` al comenzar: contiene el contexto, las reglas del usuario, la arquitectura y el estado del proyecto. Mantén estas instrucciones coherentes con ese archivo.

- Trabaja y documenta en español. No hagas push salvo petición explícita; no incluyas atribuciones de IA en los commits.
- Protege claves, contraseñas y datos personales: nunca los incluyas en código, registros, documentación ni respuestas. No versiones bases de datos, entornos, dependencias instaladas, imágenes ni resultados de pruebas.
- La plataforma es de investigación personal: no es asesoría financiera, no ejecuta operaciones reales y no conecta brokers. Toda cifra debe declarar su procedencia y antigüedad.
- Conserva las garantías de Quant Lab: datos disponibles hasta cada fecha, purga temporal, selección por validación, evaluación fuera de muestra y experimentos reproducibles e inmutables. Paper trading permanece aislado de brokers y cartera real.
- Usa los entornos existentes: Django en `apps/backend/.venv`, LightGBM en `workers/qlib/.venv` y Qlib en `workers/qlib/.venv311`. Consulta `docs/runbooks/quant-lab.md` antes de entrenar o cambiar la evaluación.
- Para el arranque local usa normalmente backend en 8010 y web en 3010; configura CSRF y `BACKEND_URL` según el `README.md`. Evita ejecutar Qlib, Docker y Ollama juntos en equipos con poca memoria.
- Antes de modificar el frontend, sigue además `apps/web/AGENTS.md` y consulta la documentación de Next instalada en `apps/web/node_modules/next/dist/docs/`.
- Ejecuta las pruebas pertinentes al cambio; antes de un commit sigue `.claude/skills/repo-limpio/SKILL.md`.
