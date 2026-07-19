from rest_framework import serializers


class RealtimeTicketRequestSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    device_type = serializers.CharField(max_length=32, required=False, allow_blank=True, default="unknown")


class RealtimeAudienceSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(
        choices=["conversation", "user", "support_website", "support_user"]
    )
    id = serializers.CharField(max_length=160)


class RealtimeGrantRequestSerializer(serializers.Serializer):
    audiences = RealtimeAudienceSerializer(many=True, min_length=1, max_length=100)


class RealtimeCallGrantRequestSerializer(serializers.Serializer):
    call_id = serializers.UUIDField()
