import sqlite3
from datetime import UTC, datetime, timedelta
from io import StringIO
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command

from modules.instruments.models import Instrument, InstrumentIdentifier

from . import backup as bk

KEY = bk.generate_key()
OTRA = bk.generate_key()


@pytest.fixture
def datos(db):
    user = get_user_model().objects.create_user("ana", password="x")
    for i in range(5):
        ins = Instrument.objects.create(asset_class="STOCK", name=f"E{i}", quantity_scale=2)
        InstrumentIdentifier.objects.create(instrument=ins, kind="TICKER", value=f"T{i}")
    return user


def test_clave_valida_e_invalida(monkeypatch):
    assert len(KEY) == 44 and KEY != OTRA
    monkeypatch.delenv("BACKUP_KEY", raising=False)
    with pytest.raises(bk.BackupError, match="Falta BACKUP_KEY"):
        bk.load_key()
    for mala in ("abc", "x" * 44, KEY[:-4]):
        with pytest.raises(bk.BackupError, match="inválida"):
            bk.load_key(mala)


def test_respaldo_cifrado_se_restaura_con_los_mismos_datos(datos, tmp_path):
    out = bk.create_backup(tmp_path / "bk", KEY, now=datetime(2026, 11, 15, 12, 0, tzinfo=UTC))
    assert out.name == "asi-backup-20261115T120000Z.asibak" and out.read_bytes().startswith(bk.MAGIC)
    assert (
        b"ana" not in out.read_bytes() and b"SQLite" not in out.read_bytes()
    )  # cifrado: no se lee nada a simple vista
    res = bk.verify_backup(out, KEY)
    assert res["ok"] is True and res["problems"] == [] and res["rows"] > 10 and res["migrations"] > 20
    destino = bk.restore_backup(out, tmp_path / "restaurada.sqlite3", KEY)
    con = sqlite3.connect(destino)
    try:
        assert con.execute("SELECT COUNT(*) FROM instruments_instrument").fetchone()[0] == 5
        assert con.execute("SELECT username FROM auth_user").fetchone()[0] == "ana"
        assert (
            con.execute("SELECT value FROM instruments_instrumentidentifier ORDER BY value").fetchall()[0][0]
            == "T0"
        )
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


def test_el_respaldo_refleja_el_momento_de_la_copia_no_datos_posteriores(datos, tmp_path):
    out = bk.create_backup(tmp_path, KEY)
    Instrument.objects.create(asset_class="STOCK", name="Nueva", quantity_scale=2)  # después del respaldo
    destino = bk.restore_backup(out, tmp_path / "r.sqlite3", KEY)
    con = sqlite3.connect(destino)
    try:
        assert con.execute("SELECT COUNT(*) FROM instruments_instrument").fetchone()[0] == 5
    finally:
        con.close()


def test_clave_incorrecta_archivo_alterado_o_truncado_fallan_con_mensaje_claro(datos, tmp_path):
    out = bk.create_backup(tmp_path, KEY)
    with pytest.raises(bk.BackupError, match="clave incorrecta o archivo alterado"):
        bk.verify_backup(out, OTRA)
    raw = bytearray(out.read_bytes())
    raw[len(bk.MAGIC) + 200] ^= 0xFF  # un solo byte cambiado
    alterado = tmp_path / "alterado.asibak"
    alterado.write_bytes(bytes(raw))
    with pytest.raises(bk.BackupError, match="alterado"):
        bk.verify_backup(alterado, KEY)
    truncado = tmp_path / "truncado.asibak"
    truncado.write_bytes(out.read_bytes()[:-50])
    with pytest.raises(bk.BackupError, match="alterado|incompleto"):
        bk.verify_backup(truncado, KEY)
    ajeno = tmp_path / "ajeno.asibak"
    ajeno.write_bytes(b"esto no es un respaldo")
    with pytest.raises(bk.BackupError, match="formato desconocido"):
        bk.verify_backup(ajeno, KEY)


def test_la_verificacion_detecta_un_respaldo_con_datos_distintos_al_manifiesto(datos, tmp_path, monkeypatch):
    monkeypatch.setattr(
        bk, "_sqlite_counts", lambda path: {"instruments_instrument": 999}
    )  # manifiesto falso al crear
    out = bk.create_backup(tmp_path, KEY)
    monkeypatch.undo()
    res = bk.verify_backup(out, KEY)
    assert res["ok"] is False and any("filas distintas" in p for p in res["problems"])


def test_nunca_pisa_la_base_en_uso_ni_un_archivo_existente(datos, tmp_path, monkeypatch):
    out = bk.create_backup(tmp_path, KEY)
    en_uso = tmp_path / "en_uso.sqlite3"
    en_uso.write_bytes(b"datos vivos")
    monkeypatch.setattr(
        bk, "settings", SimpleNamespace(DATABASES={"default": {"NAME": str(en_uso)}})
    )  # sin tocar la conexión real
    with pytest.raises(bk.BackupError, match="base en uso"):
        bk.restore_backup(out, en_uso, KEY, force=True)  # ni con --force
    assert en_uso.read_bytes() == b"datos vivos"
    existente = tmp_path / "existente.sqlite3"
    existente.write_bytes(b"x")
    with pytest.raises(bk.BackupError, match="ya existe"):
        bk.restore_backup(out, existente, KEY)
    bk.restore_backup(out, existente, KEY, force=True)
    assert existente.read_bytes()[:6] == b"SQLite"


def test_retencion_conserva_solo_los_mas_recientes_y_no_toca_otros_archivos(datos, tmp_path):
    base = datetime(2026, 11, 1, tzinfo=UTC)
    for d in range(5):
        bk.create_backup(tmp_path, KEY, now=base + timedelta(days=d))
    ajeno = tmp_path / "notas.txt"
    ajeno.write_text("no borrar")
    borrados = bk.prune(tmp_path, 2)
    quedan = sorted(p.name for p in tmp_path.glob("asi-backup-*"))
    assert len(borrados) == 3 and quedan == [
        "asi-backup-20261104T000000Z.asibak",
        "asi-backup-20261105T000000Z.asibak",
    ]
    assert ajeno.exists()
    with pytest.raises(bk.BackupError):
        bk.prune(tmp_path, 0)


def test_no_quedan_archivos_parciales(datos, tmp_path):
    bk.create_backup(tmp_path, KEY)
    assert not list(tmp_path.glob("*.part"))


def test_comandos_de_extremo_a_extremo(datos, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKUP_KEY", KEY)
    out = StringIO()
    call_command("backup_db", "--dest", str(tmp_path), "--keep", "3", stdout=out)
    assert "Respaldo verificado" in out.getvalue()
    archivo = next(tmp_path.glob("asi-backup-*.asibak"))
    out = StringIO()
    call_command("verify_backup", str(archivo), stdout=out)
    assert "restaurable" in out.getvalue()
    call_command("restore_db", str(archivo), "--target", str(tmp_path / "nueva.sqlite3"), stdout=StringIO())
    assert (tmp_path / "nueva.sqlite3").exists()
    with pytest.raises(CommandError, match="ya existe"):
        call_command(
            "restore_db", str(archivo), "--target", str(tmp_path / "nueva.sqlite3"), stdout=StringIO()
        )
    monkeypatch.setenv("BACKUP_KEY", OTRA)
    with pytest.raises(CommandError, match="clave incorrecta"):
        call_command("verify_backup", str(archivo), stdout=StringIO())
    monkeypatch.delenv("BACKUP_KEY")
    with pytest.raises(CommandError, match="Falta BACKUP_KEY"):
        call_command("backup_db", "--dest", str(tmp_path), stdout=StringIO())


def test_generar_clave_no_escribe_archivos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = StringIO()
    call_command("generate_backup_key", stdout=out, stderr=StringIO())
    assert bk.load_key(out.getvalue().strip()) and not list(tmp_path.iterdir())


# ------------------------------------------------------------------------------------------ PostgreSQL (con dobles)
class FakeProc:
    def __init__(self, out=b"", err=b"", code=0):
        self.stdout, self.stderr, self.returncode = out, err, code


def test_pg_run_no_pone_la_contrasena_en_los_argumentos_y_falla_con_mensaje_redactado(monkeypatch):
    visto = {}

    def falso(cmd, **kw):
        visto.update(cmd=cmd, env=kw["env"])
        return FakeProc(code=1, err=b"FATAL: password=hunter2 incorrecta")

    monkeypatch.setattr(bk.shutil, "which", lambda t: f"/usr/bin/{t}")
    monkeypatch.setattr(bk.subprocess, "run", falso)
    with pytest.raises(bk.BackupError) as e:
        bk._pg_run("pg_dump", ["-h", "db"], password="hunter2")
    assert "hunter2" not in " ".join(visto["cmd"]) and visto["env"]["PGPASSWORD"] == "hunter2"
    assert "hunter2" not in str(e.value) and "pg_dump falló" in str(e.value)
    monkeypatch.setattr(bk.shutil, "which", lambda t: None)
    with pytest.raises(bk.BackupError, match="No está instalado pg_dump"):
        bk._pg_run("pg_dump", [])


def respaldo_pg(tmp_path, monkeypatch, tablas=None):
    tablas = tablas or {"portfolio_transaction": 5, "auth_user": 1}
    monkeypatch.setattr(bk, "connection", SimpleNamespace(vendor="postgresql"))
    monkeypatch.setattr(bk, "_pg_snapshot", lambda: (b"PGDMP-falso", tablas, ["app.0001_initial"]))
    return bk.create_backup(tmp_path, KEY)


def test_respaldo_de_postgres_guarda_el_motor_y_la_verificacion_es_superficial_y_se_declara(
    tmp_path, monkeypatch
):
    out = respaldo_pg(tmp_path, monkeypatch)
    manifest, dump = bk.open_backup(out, KEY)
    assert (
        manifest["engine"] == "postgresql" and dump == b"PGDMP-falso" and manifest["tables"]["auth_user"] == 1
    )
    toc = b"; Archive created\n1; 1259 16385 TABLE DATA public portfolio_transaction asi\n2; 1259 16390 TABLE DATA public auth_user asi\n"
    monkeypatch.setattr(bk, "_pg_run", lambda tool, args, password="", stdin=None: toc)
    res = bk.verify_backup(out, KEY)
    assert res["ok"] is True and res["deep"] is False and res["rows"] == 6
    monkeypatch.setattr(
        bk, "_pg_run", lambda *a, **k: b"1; 1 1 TABLE DATA public auth_user asi\n"
    )  # falta una tabla
    res = bk.verify_backup(out, KEY)
    assert res["ok"] is False and "portfolio_transaction" in res["problems"][0]


def test_restaurar_postgres_crea_base_nueva_en_orden_y_nunca_toca_la_en_uso(tmp_path, monkeypatch):
    out = respaldo_pg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bk,
        "settings",
        SimpleNamespace(
            DATABASES={
                "default": {"HOST": "db", "PORT": 5432, "USER": "asi", "NAME": "asi", "PASSWORD": "pw"}
            }
        ),
    )
    llamadas = []
    monkeypatch.setattr(
        bk,
        "_pg_run",
        lambda tool, args, password="", stdin=None: llamadas.append((tool, args, password)) or b"",
    )
    with pytest.raises(bk.BackupError, match="base en uso"):
        bk.restore_postgres(out, "asi", KEY)
    for mala in ("a; drop database asi", "x-y", "a b", ""):
        with pytest.raises(bk.BackupError, match="inválido"):
            bk.restore_postgres(out, mala, KEY)
    assert llamadas == []  # nada se ejecutó con nombres peligrosos
    assert bk.restore_postgres(out, "asi_restore_test", KEY) == "asi_restore_test"
    assert (
        [c[0] for c in llamadas] == ["createdb", "pg_restore"]
        and llamadas[0][1][-1] == "asi_restore_test"
        and "-d" in llamadas[1][1]
    )
    assert all(c[2] == "pw" for c in llamadas)


def test_un_respaldo_de_postgres_no_se_restaura_como_sqlite_ni_al_reves(tmp_path, monkeypatch, datos):
    pg = respaldo_pg(tmp_path, monkeypatch)
    with pytest.raises(bk.BackupError, match="restore_db --pg-database"):
        bk.restore_backup(pg, tmp_path / "x.sqlite3", KEY)
    monkeypatch.undo()
    lite = bk.create_backup(tmp_path / "lite", KEY)
    monkeypatch.setattr(bk, "settings", SimpleNamespace(DATABASES={"default": {"NAME": "otra"}}))
    with pytest.raises(bk.BackupError, match="no es de PostgreSQL"):
        bk.restore_postgres(lite, "asi_prueba", KEY)
