"""Respaldos cifrados de la base de datos y verificación de restauración.

Formato: `ASIBK1\\n` + token Fernet (AES-128-CBC + HMAC-SHA256: cifrado Y autenticado, así que un archivo alterado o con
otra clave falla de forma explícita). Dentro: un JSON con el manifiesto (fecha, motor, migraciones, filas por tabla,
SHA-256 del volcado) y el volcado comprimido con gzip.

La clave (`BACKUP_KEY`) vive solo en el entorno o en tu gestor de contraseñas: nunca se escribe en archivos ni en el
repositorio. Sin clave no hay respaldo. Un respaldo no probado no es un respaldo: `verify_backup` lo restaura en un
archivo temporal, corre `PRAGMA integrity_check` y compara filas por tabla con el manifiesto.

SQLite (desarrollo y demo) se respalda con la API de copia en línea de sqlite3 (instantánea consistente aun con la app
corriendo). PostgreSQL usa `pg_dump -Fc` y `pg_restore` si están instalados (ver docs/runbooks/despliegue-remoto.md)."""

import base64
import gzip
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import connection

MAGIC = b"ASIBK1\n"
NAME_PREFIX = "asi-backup-"
SUFFIX = ".asibak"
MAX_BYTES = 2_000_000_000


class BackupError(Exception):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


def load_key(key: str | None = None) -> Fernet:
    key = key or os.environ.get("BACKUP_KEY", "")
    if not key:
        raise BackupError(
            "Falta BACKUP_KEY (genera una con `manage.py generate_backup_key` y guárdala en tu gestor de contraseñas)."
        )
    try:
        raw = base64.urlsafe_b64decode(key.encode())
        if len(raw) != 32:
            raise ValueError
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise BackupError("BACKUP_KEY inválida: debe ser una clave Fernet de 32 bytes en base64.") from None


# ------------------------------------------------------------------------------------------------------ SQLite
def _sqlite_counts(path: str) -> dict:
    con = sqlite3.connect(path)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()


def _sqlite_snapshot() -> tuple[bytes, dict]:
    """Instantánea consistente de la base SQLite activa (también funciona con la base en memoria de las pruebas)."""
    if connection.vendor != "sqlite":
        raise BackupError(
            "Este respaldo integrado cubre SQLite; para PostgreSQL usa pg_dump (ver el runbook de despliegue)."
        )
    connection.ensure_connection()
    fd, tmp = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        dest = sqlite3.connect(tmp)
        try:
            name = str(connection.settings_dict.get("NAME", ""))
            if name == ":memory:" or "mode=memory" in name or name.startswith("file:memorydb"):
                # Base en memoria (solo pruebas): la copia en línea se bloquea con caché compartida; se vuelca como SQL.
                dest.executescript(chr(10).join(connection.connection.iterdump()))
            else:
                connection.connection.backup(dest)  # instantánea consistente aunque la app siga escribiendo
        finally:
            dest.close()
        counts = _sqlite_counts(tmp)
        return Path(tmp).read_bytes(), counts
    finally:
        Path(tmp).unlink(missing_ok=True)


def _migrations(counts_source_bytes: bytes) -> list[str]:
    fd, tmp = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        Path(tmp).write_bytes(counts_source_bytes)
        con = sqlite3.connect(tmp)
        try:
            return [f"{a}.{n}" for a, n in con.execute("SELECT app, name FROM django_migrations ORDER BY id")]
        except sqlite3.Error:
            return []
        finally:
            con.close()
    finally:
        Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------------------------- crear / abrir
def create_backup(dest_dir: str | Path, key: str | None = None, now: datetime | None = None) -> Path:
    f = load_key(key)
    dump, counts = _sqlite_snapshot()
    if len(dump) > MAX_BYTES:
        raise BackupError(
            "La base es demasiado grande para este método; usa pg_dump con almacenamiento externo."
        )
    now = now or datetime.now(UTC)
    manifest = {
        "version": 1, "created_at": now.isoformat(), "engine": "sqlite", "bytes": len(dump), "sha256": hashlib.sha256(dump).hexdigest(),
        "tables": counts, "migrations": _migrations(dump),
    }  # fmt: skip
    payload = json.dumps(manifest).encode() + b"\n" + gzip.compress(dump, 6)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{NAME_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}{SUFFIX}"
    tmp = out.with_suffix(out.suffix + ".part")
    tmp.write_bytes(MAGIC + f.encrypt(payload))
    tmp.replace(out)  # escritura atómica: nunca queda un respaldo a medias con el nombre final
    return out


def open_backup(path: str | Path, key: str | None = None) -> tuple[dict, bytes]:
    """Descifra, autentica y valida. Devuelve (manifiesto, volcado SQLite). Lanza BackupError ante cualquier problema."""
    f = load_key(key)
    raw = Path(path).read_bytes()
    if not raw.startswith(MAGIC):
        raise BackupError("No es un respaldo de AI Stock Intelligence (formato desconocido).")
    try:
        payload = f.decrypt(raw[len(MAGIC) :])
    except InvalidToken:
        raise BackupError("No se pudo descifrar: clave incorrecta o archivo alterado/incompleto.") from None
    head, _, body = payload.partition(b"\n")
    try:
        manifest = json.loads(head)
        dump = gzip.decompress(body)
    except (ValueError, OSError, EOFError):
        raise BackupError("Contenido del respaldo dañado.") from None
    if hashlib.sha256(dump).hexdigest() != manifest.get("sha256") or len(dump) != manifest.get("bytes"):
        raise BackupError("La suma de verificación no coincide: respaldo dañado.")
    return manifest, dump


def verify_backup(path: str | Path, key: str | None = None) -> dict:
    """Prueba de restauración: abre el respaldo, lo restaura en un archivo temporal y comprueba integridad y filas por tabla."""
    manifest, dump = open_backup(path, key)
    fd, tmp = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        Path(tmp).write_bytes(dump)
        con = sqlite3.connect(tmp)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            fk = con.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            con.close()
        counts = _sqlite_counts(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)
    problems = []
    if integrity != "ok":
        problems.append(f"integrity_check: {integrity}")
    if fk:
        problems.append(f"{len(fk)} violaciones de llaves foráneas")
    if counts != manifest["tables"]:
        diff = sorted(
            t for t in set(counts) | set(manifest["tables"]) if counts.get(t) != manifest["tables"].get(t)
        )
        problems.append(f"filas distintas al manifiesto en: {', '.join(diff)}")
    return {
        "ok": not problems, "problems": problems, "created_at": manifest["created_at"], "tables": len(counts),
        "rows": sum(counts.values()), "migrations": len(manifest["migrations"]),
    }  # fmt: skip


def restore_backup(path: str | Path, target: str | Path, key: str | None = None, force: bool = False) -> Path:
    """Restaura a un archivo NUEVO. Nunca sobrescribe la base en uso ni un archivo existente salvo `force`."""
    target = Path(target).resolve()
    live = settings.DATABASES["default"].get("NAME")
    if live and Path(str(live)).resolve() == target:
        raise BackupError(
            "El destino es la base en uso. Restaura a otro archivo, detén la app y reemplaza manualmente."
        )
    if target.exists() and not force:
        raise BackupError(f"{target} ya existe (usa --force para reemplazarlo).")
    manifest, dump = open_backup(path, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(dump)
    tmp.replace(target)
    if _sqlite_counts(str(target)) != manifest["tables"]:
        target.unlink(missing_ok=True)
        raise BackupError("La restauración no coincide con el manifiesto; se descartó.")
    return target


def prune(dest_dir: str | Path, keep: int) -> list[Path]:
    """Conserva los `keep` respaldos más recientes (solo archivos con el nombre de esta aplicación)."""
    if keep < 1:
        raise BackupError("keep debe ser al menos 1.")
    files = sorted(Path(dest_dir).glob(f"{NAME_PREFIX}*{SUFFIX}"), reverse=True)
    old = files[keep:]
    for p in old:
        p.unlink()
    return old
