"""Cuota compartida por proceso y usuario para los proveedores externos."""

import math
import os
import time

from django.db import OperationalError, transaction
from django.db.models import F

from .models import ProviderQuota


class QuotaUnavailable(Exception):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def reserve(provider):
    limits = {
        "TWELVE_DATA": ("TWELVE_DATA_PER_MINUTE", 8),
        "YAHOO_UNOFFICIAL": ("YAHOO_PER_MINUTE", 30),
        "BANXICO_FIX": ("BANXICO_PER_MINUTE", 10),
    }
    env, default = limits[provider]
    try:
        limit = max(1, min(int(os.getenv(env, str(default))), 1000))
    except ValueError:
        limit = default
    minute = int(time.time() // 60)
    try:
        with transaction.atomic():
            row, _ = ProviderQuota.objects.get_or_create(provider=provider, minute=minute)
            used = ProviderQuota.objects.filter(pk=row.pk, used__lt=limit).update(used=F("used") + 1)
            if minute % 60 == 0:
                ProviderQuota.objects.filter(minute__lt=minute - 1440).delete()
    except OperationalError:
        raise QuotaUnavailable("No se pudo reservar la cuota del proveedor; intenta más tarde.") from None
    if not used:
        retry_after = max(1, math.ceil((minute + 1) * 60 - time.time()))
        raise QuotaUnavailable("Límite de consultas del proveedor alcanzado; espera un minuto.", retry_after)
