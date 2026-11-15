from rest_framework import mixins, serializers, viewsets

from .models import InvestmentAccount


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvestmentAccount
        fields = ["id", "name", "broker", "base_currency", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        owner = self.context["request"].user
        if InvestmentAccount.objects.filter(owner=owner, name=attrs.get("name")).exists():
            raise serializers.ValidationError({"name": "Ya tienes una cuenta con ese nombre."})
        return attrs


class AccountViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = AccountSerializer

    def get_queryset(self):
        # Solo las cuentas del usuario autenticado.
        return InvestmentAccount.objects.filter(owner=self.request.user).order_by("id")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)
