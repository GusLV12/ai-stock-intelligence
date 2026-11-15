import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()  # los contadores de throttling no deben filtrarse entre pruebas
