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
