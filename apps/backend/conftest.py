import urllib.error
import urllib.request

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()  # los contadores de throttling no deben filtrarse entre pruebas


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Ninguna prueba debe salir a internet (proveedores, Yahoo, Ollama…): se simulan con dobles.
    Se lanza URLError, el mismo error que daría un servidor caído, para que los caminos de fallo se prueben."""

    def blocked(*args, **kwargs):
        raise urllib.error.URLError("red bloqueada en pruebas")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
