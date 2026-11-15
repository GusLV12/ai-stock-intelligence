from django.db import DatabaseError, connection
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Estado del servicio. No expone datos financieros ni de configuración."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        db = "ok"
    except DatabaseError:
        db = "error"
    return Response({"status": "ok" if db == "ok" else "degraded", "database": db})
