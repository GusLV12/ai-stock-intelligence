"""Cliente mínimo de Ollama (API /api/chat). Local-first: por defecto solo acepta un servidor en esta
misma máquina, porque a ese servidor se le envían datos financieros del usuario."""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from ipaddress import ip_address

MAX_BYTES = 4_000_000
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OllamaError(Exception):
    """El modelo local no está disponible o respondió mal. Los cálculos financieros siguen funcionando."""


def _validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise OllamaError("OLLAMA_URL inválida.")
    host = parsed.hostname
    is_local = host in LOCAL_HOSTS
    if not is_local:
        try:
            is_local = ip_address(host).is_loopback
        except ValueError:
            is_local = False
    if not is_local and os.getenv("OLLAMA_ALLOW_REMOTE", "").lower() not in ("1", "true", "yes"):
        raise OllamaError(
            "OLLAMA_URL apunta fuera de esta máquina; tus datos financieros no se enviarán ahí "
            "(define OLLAMA_ALLOW_REMOTE=true solo si lo decides conscientemente)."
        )
    return url.rstrip("/")


def post_json(url: str, payload: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - http/https validado
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise OllamaError(f"Ollama respondió HTTP {exc.code} (¿el modelo está descargado?).") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OllamaError("No se pudo conectar con Ollama (¿está iniciado?).") from None
    if len(raw) > MAX_BYTES:
        raise OllamaError("Respuesta de Ollama demasiado grande.")
    try:
        data = json.loads(raw)
    except ValueError:
        raise OllamaError("Respuesta de Ollama no es JSON válido.") from None
    if not isinstance(data, dict):
        raise OllamaError("Respuesta inesperada de Ollama.")
    return data


class OllamaClient:
    def __init__(
        self, base_url=None, model=None, timeout=None, num_ctx=None, num_predict=None, http=post_json
    ):
        self.base_url = _validate_url(base_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"))
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen3:1.7b")
        self.timeout = int(timeout or os.getenv("OLLAMA_TIMEOUT", "180"))
        self.num_ctx = int(num_ctx or os.getenv("OLLAMA_NUM_CTX", "4096"))
        self.num_predict = int(num_predict or os.getenv("OLLAMA_NUM_PREDICT", "700"))
        self._http = http

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """Devuelve el mensaje del asistente: {'content': str, 'tool_calls': [...]}."""
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "think": False,  # sin razonamiento largo: menos tokens y latencia en CPU
            "options": {"num_ctx": self.num_ctx, "num_predict": self.num_predict, "temperature": 0.2},
        }
        data = self._http(f"{self.base_url}/api/chat", payload, self.timeout)
        msg = data.get("message")
        if not isinstance(msg, dict):
            raise OllamaError("Ollama no devolvió un mensaje.")
        return {"content": str(msg.get("content") or ""), "tool_calls": msg.get("tool_calls") or []}
