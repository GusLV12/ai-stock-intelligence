from rest_framework import mixins, serializers, viewsets

from .models import Instrument, InstrumentIdentifier


class IdentifierSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstrumentIdentifier
        fields = ["kind", "value"]


class InstrumentSerializer(serializers.ModelSerializer):
    identifiers = IdentifierSerializer(many=True, required=False)

    class Meta:
        model = Instrument
        fields = ["id", "asset_class", "name", "market", "currency", "quantity_scale", "identifiers"]
        read_only_fields = ["id"]

    def validate_quantity_scale(self, value):
        if value > 18:
            raise serializers.ValidationError("Máximo 18 decimales.")
        return value

    def create(self, validated_data):
        identifiers = validated_data.pop("identifiers", [])
        instrument = Instrument.objects.create(**validated_data)
        for ident in identifiers:
            InstrumentIdentifier.objects.create(instrument=instrument, **ident)
        return instrument


class InstrumentViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = InstrumentSerializer
    queryset = Instrument.objects.prefetch_related("identifiers").order_by("id")
