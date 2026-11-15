"""Autenticación por sesión de Django. El login exige CSRF y tiene límite de intentos."""

from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView


class LoginThrottle(AnonRateThrottle):
    scope = "login"


def _user_data(user):
    return {"id": user.id, "username": user.get_username()}


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    """Entrega el token CSRF (también como cookie) para que el frontend pueda hacer POST."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"csrf_token": get_token(request)})


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [LoginThrottle]

    def post(self, request):
        username = str(request.data.get("username", ""))
        password = str(request.data.get("password", ""))
        user = authenticate(request, username=username, password=password)
        if user is None or not user.is_active:
            # Mensaje único: no revela si el usuario existe.
            raise serializers.ValidationError({"detail": "Usuario o contraseña incorrectos."})
        login(request, user)
        return Response(_user_data(user))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=204)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_user_data(request.user))
