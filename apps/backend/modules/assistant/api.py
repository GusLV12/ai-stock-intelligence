import time

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .agent import MAX_CHARS, run_chat
from .models import AssistantLog
from .ollama import OllamaClient, OllamaError

MAX_HISTORY_ITEMS = 12


class AssistantThrottle(UserRateThrottle):
    scope = "assistant"


def get_client() -> OllamaClient:
    """Punto único de construcción del cliente (las pruebas lo sustituyen por un modelo simulado)."""
    return OllamaClient()


class ChatView(APIView):
    throttle_classes = [AssistantThrottle]

    def post(self, request):
        message = request.data.get("message")
        if not isinstance(message, str) or not message.strip():
            raise serializers.ValidationError({"message": "Escribe una pregunta."})
        if len(message) > MAX_CHARS:
            raise serializers.ValidationError({"message": f"Máximo {MAX_CHARS} caracteres."})
        history = request.data.get("history") or []
        if not isinstance(history, list) or len(history) > MAX_HISTORY_ITEMS:
            raise serializers.ValidationError({"history": f"Debe ser una lista de hasta {MAX_HISTORY_ITEMS} mensajes."})

        started = time.monotonic()
        client = None
        try:
            client = get_client()
            result = run_chat(request.user, message.strip(), history, client)
        except OllamaError as exc:
            self._log(request, message, client, [], 0, 0, AssistantLog.Status.UNAVAILABLE, started)
            return Response(
                {"detail": str(exc), "note": "El modelo local no está disponible; el resto de la app sigue funcionando."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        self._log(
            request, message, client, [c["name"] for c in result.tool_calls], len(result.unverified_numbers),
            result.steps, AssistantLog.Status.OK, started,
        )  # fmt: skip
        return Response(
            {
                "answer": result.answer,
                "model": client.model,
                "tool_calls": result.tool_calls,
                "sources": result.sources,
                "unverified_numbers": result.unverified_numbers,
                "guardrail": result.guardrail,
                "verification_note": (
                    "Las cifras listadas en unverified_numbers no aparecen en los datos de las herramientas: "
                    "no las tomes como válidas."
                    if result.unverified_numbers
                    else None
                ),
            }
        )

    @staticmethod
    def _log(request, message, client, tools, unverified, steps, status_, started):
        AssistantLog.objects.create(
            user=request.user, question=message.strip()[:300], model=getattr(client, "model", "")[:60],
            tools=tools, unverified_count=unverified, steps=steps, status=status_,
            duration_ms=int((time.monotonic() - started) * 1000),
        )  # fmt: skip
