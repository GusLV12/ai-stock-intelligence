import asyncio
import time
from datetime import timedelta
from io import StringIO

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.utils import timezone

from modules.accounts.models import InvestmentAccount

from . import services as svc
from .models import McpAuditLog, McpCredential
from .oauth import JwtTokenVerifier

ISSUER = "https://idp.example.com"
AUDIENCE = "https://mcp.example.com/mcp"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTRA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC = ec.generate_private_key(ec.SECP256R1())
PUBLIC_PEM = KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)


def pem(k):
    return k.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def token(key=KEY, alg="RS256", **over):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "exp": now + 300,
        "nbf": now - 5,
        "scope": "portfolio:read transactions:read",
        "azp": "claude",
    }
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, pem(key) if not isinstance(key, bytes) else key, algorithm=alg)


def forjar_hs256():
    """Ataque de confusión de algoritmo: firma HS256 usando la llave PÚBLICA como secreto HMAC (PyJWT se niega a
    hacerlo, un atacante no: se arma a mano)."""
    import base64
    import hashlib
    import hmac
    import json

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(
        json.dumps({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 60}).encode()
    )
    firma = hmac.new(PUBLIC_PEM, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(firma)}"


def verifier(**kw):
    def resolver(tok):  # llaves públicas "del proveedor": solo la correcta
        return {"RS": KEY, "ES": EC}.get(jwt.get_unverified_header(tok)["alg"][:2], KEY).public_key()

    return JwtTokenVerifier(ISSUER, AUDIENCE, key_resolver=resolver, **kw)


# ----------------------------------------------------------------------------------------------- verificación
def test_token_valido_entrega_identidad_y_scopes_conocidos():
    t = verifier().verify_sync(token(scope="portfolio:read inventado transactions:read"))
    assert (
        t.subject == "user-1"
        and t.claims == {"iss": ISSUER}
        and t.client_id == "claude"
        and t.resource == AUDIENCE
    )
    assert t.scopes == ["portfolio:read", "transactions:read"]  # los scopes desconocidos se descartan


def test_scopes_como_lista_y_en_scp():
    assert verifier().verify_sync(token(scope=None, scp=["calculators:run"])).scopes == ["calculators:run"]
    assert verifier().verify_sync(token(scope=None)).scopes == []


@pytest.mark.parametrize(
    "tok",
    [
        pytest.param(lambda: token(exp=int(time.time()) - 3600), id="vencido"),
        pytest.param(lambda: token(nbf=int(time.time()) + 3600), id="aun_no_valido"),
        pytest.param(
            lambda: token(aud="https://otra-api.example.com"), id="audiencia_ajena_token_passthrough"
        ),
        pytest.param(lambda: token(iss="https://evil.example.com"), id="emisor_distinto"),
        pytest.param(lambda: token(sub=None), id="sin_sub"),
        pytest.param(lambda: token(exp=None), id="sin_exp"),
        pytest.param(lambda: token(aud=None), id="sin_aud"),
        pytest.param(lambda: token(key=OTRA), id="firmado_por_otra_llave"),
        pytest.param(
            lambda: jwt.encode(
                {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": int(time.time()) + 60},
                None,
                algorithm="none",
            ),
            id="alg_none",
        ),
        pytest.param(lambda: forjar_hs256(), id="confusion_HS256_con_llave_publica"),
        pytest.param(lambda: "no.es.jwt", id="basura"),
        pytest.param(lambda: "", id="vacio"),
    ],
)
def test_tokens_invalidos_se_rechazan(tok):
    assert verifier().verify_sync(tok()) is None


def test_curva_eliptica_tambien_es_valida():
    assert verifier().verify_sync(token(key=EC, alg="ES256")).subject == "user-1"


def test_si_las_llaves_del_proveedor_no_se_pueden_obtener_se_rechaza():
    def roto(_):
        raise ConnectionError("JWKS inalcanzable")

    assert JwtTokenVerifier(ISSUER, AUDIENCE, key_resolver=roto).verify_sync(token()) is None


@pytest.mark.parametrize(
    "issuer,audience",
    [("", AUDIENCE), (ISSUER, ""), ("http://idp.example.com", AUDIENCE), ("ftp://x", AUDIENCE)],
)
def test_configuracion_insegura_no_arranca(issuer, audience):
    with pytest.raises(ValueError):
        JwtTokenVerifier(issuer, audience, key_resolver=lambda t: None)
    assert JwtTokenVerifier("http://localhost:8080/realms/x", AUDIENCE, key_resolver=lambda t: None)


def test_verify_token_async_usa_la_misma_logica():
    async def ambos():
        return await verifier().verify_token(token()), await verifier().verify_token(token(aud="otra"))

    ok, mal = asyncio.run(ambos())
    assert ok.subject == "user-1" and mal is None


# -------------------------------------------------------------------------------------- permisos y vinculación
@pytest.fixture
def mundo(db):
    ana = get_user_model().objects.create_user("ana", password="x")
    acc = InvestmentAccount.objects.create(owner=ana, name="GBM", broker="GBM_USA")
    cred = svc.create_oauth_binding(ana, ISSUER, "user-1", "Claude remoto", ["portfolio:read"], [acc])
    return ana, acc, cred


def correr(
    scope="portfolio:read",
    token_scopes=("portfolio:read", "transactions:read"),
    sub="user-1",
    issuer=ISSUER,
    fn=None,
):
    return svc.run_tool_oauth(
        issuer, sub, token_scopes, "t", scope, fn or (lambda cred: {"ok": cred.owner.username}), {}
    )


def test_el_token_no_amplia_los_permisos_de_la_credencial(mundo):
    assert correr() == {"ok": "ana"}
    with pytest.raises(svc.McpPermissionError, match="no tiene el scope transactions:read"):
        correr(scope="transactions:read")  # el token lo trae, la credencial no lo autorizó
    assert McpAuditLog.objects.filter(status="denied").count() == 1


def test_la_credencial_no_amplia_los_permisos_del_token(mundo):
    McpCredential.objects.update(scopes=["portfolio:read", "transactions:read"])
    with pytest.raises(svc.McpPermissionError, match="El token no incluye"):
        correr(scope="transactions:read", token_scopes=("portfolio:read",))  # la credencial sí, el token no


@pytest.mark.parametrize(
    "sub,issuer", [("otro", ISSUER), ("user-1", "https://otro.example.com"), ("", ISSUER), ("user-1", "")]
)
def test_identidad_no_vinculada_falla_con_mensaje_generico_y_se_audita(mundo, sub, issuer):
    with pytest.raises(svc.McpAuthError, match="inválida, vencida o revocada"):
        correr(sub=sub, issuer=issuer)
    assert McpAuditLog.objects.filter(status="auth_failed").count() == 1


def test_revocada_vencida_o_usuario_inactivo_dejan_de_funcionar_de_inmediato(mundo):
    ana, _, cred = mundo
    assert correr() == {"ok": "ana"}
    svc.revoke(cred)
    with pytest.raises(svc.McpAuthError):
        correr()
    McpCredential.objects.update(revoked_at=None, expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(svc.McpAuthError):
        correr()
    McpCredential.objects.update(expires_at=timezone.now() + timedelta(days=1))
    ana.is_active = False
    ana.save()
    with pytest.raises(svc.McpAuthError):
        correr()


def test_la_vinculacion_no_crea_una_clave_utilizable_y_solo_ve_cuentas_del_dueno(mundo):
    _ana, acc, cred = mundo
    assert cred.prefix == "oauth" and cred.oauth_subject == "user-1"
    with pytest.raises(svc.McpAuthError):
        svc.authenticate("oauth")  # no es una clave opaca válida
    otra = InvestmentAccount.objects.create(
        owner=get_user_model().objects.create_user("beto", password="x"), name="Ajena", broker="GBM_USA"
    )
    cred.accounts.add(otra)
    assert svc.allowed_account_ids(cred) == [
        acc.id
    ]  # una cuenta ajena nunca cuenta, aunque se vincule por error


def test_validaciones_de_la_vinculacion(mundo):
    ana, acc, _ = mundo
    with pytest.raises(ValueError, match="ya está vinculada"):
        svc.create_oauth_binding(ana, ISSUER, "user-1", "dup", ["portfolio:read"], [acc])
    with pytest.raises(ValueError, match="https"):
        svc.create_oauth_binding(ana, "http://idp.example.com", "u2", "x", ["portfolio:read"], [acc])
    with pytest.raises(ValueError, match="sujeto"):
        svc.create_oauth_binding(ana, ISSUER, "", "x", ["portfolio:read"], [acc])
    with pytest.raises(ValueError, match="Scopes desconocidos"):
        svc.create_oauth_binding(ana, ISSUER, "u2", "x", ["trade:write"], [acc])
    assert McpCredential.objects.count() == 1  # los intentos fallidos no dejan credenciales a medias


def test_comando_de_vinculacion(mundo):
    out = StringIO()
    call_command(
        "link_mcp_oauth",
        "--user",
        "ana",
        "--issuer",
        ISSUER,
        "--subject",
        "user-2",
        "--name",
        "Otro",
        "--scopes",
        "portfolio:read",
        "--accounts",
        "GBM",
        stdout=out,
    )
    assert (
        "Identidad vinculada" in out.getvalue()
        and McpCredential.objects.filter(oauth_subject="user-2").exists()
    )
    for args, msg in (
        (
            [
                "--user",
                "nadie",
                "--issuer",
                ISSUER,
                "--subject",
                "u",
                "--name",
                "x",
                "--scopes",
                "portfolio:read",
                "--accounts",
                "GBM",
            ],
            "Usuario",
        ),
        (
            [
                "--user",
                "ana",
                "--issuer",
                ISSUER,
                "--subject",
                "u",
                "--name",
                "x",
                "--scopes",
                "portfolio:read",
                "--accounts",
                "Inexistente",
            ],
            "Cuentas no encontradas",
        ),
        (
            [
                "--user",
                "ana",
                "--issuer",
                ISSUER,
                "--subject",
                "user-2",
                "--name",
                "x",
                "--scopes",
                "portfolio:read",
                "--accounts",
                "GBM",
            ],
            "ya está vinculada",
        ),
    ):
        with pytest.raises(CommandError, match=msg):
            call_command("link_mcp_oauth", *args, stdout=StringIO())
