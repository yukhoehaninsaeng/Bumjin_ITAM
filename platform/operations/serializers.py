from rest_framework import serializers
from .models import Asset, Ticket


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({k: "허용되지 않은 필드입니다." for k in unknown})
        return super().to_internal_value(data)


class TicketCreate(StrictSerializer):
    title = serializers.CharField(max_length=200, allow_blank=False)
    body = serializers.CharField(max_length=20000, allow_blank=False)
    type = serializers.ChoiceField(
        choices=Ticket._meta.get_field("type").choices, default="INCIDENT"
    )
    category = serializers.ChoiceField(
        choices=Ticket._meta.get_field("category").choices, default="DEVICE"
    )
    asset_ids = serializers.ListField(child=serializers.UUIDField(), max_length=20, default=list)
    attachment_ids = serializers.ListField(
        child=serializers.UUIDField(), max_length=5, default=list
    )
    draft_id = serializers.UUIDField(required=False)


class AdminTicketCreate(TicketCreate):
    requester_id = serializers.UUIDField()


class Claim(StrictSerializer):
    expected_version = serializers.IntegerField(min_value=1)


class Transition(Claim):
    status = serializers.ChoiceField(choices=Ticket.STATES)
    reason = serializers.CharField(max_length=20000, allow_blank=False)


class MessageCreate(StrictSerializer):
    body = serializers.CharField(max_length=20000, allow_blank=False)


class AdminMessageCreate(MessageCreate):
    internal = serializers.BooleanField(default=False)


class Assign(Claim):
    employee_id = serializers.UUIDField()


class TicketMetadata(Claim):
    priority = serializers.ChoiceField(choices=Ticket.PRIORITIES, required=False)
    due_at = serializers.DateTimeField(required=False, allow_null=True)


class AssetSoftwareData(StrictSerializer):
    office = serializers.CharField(max_length=120, required=False, allow_blank=True)
    hangul = serializers.CharField(max_length=120, required=False, allow_blank=True)
    sap = serializers.CharField(max_length=120, required=False, allow_blank=True)
    antivirus = serializers.CharField(max_length=120, required=False, allow_blank=True)
    docuone = serializers.CharField(max_length=120, required=False, allow_blank=True)
    officekeeper = serializers.CharField(max_length=120, required=False, allow_blank=True)
    other = serializers.CharField(max_length=500, required=False, allow_blank=True)


class AssetLegacyData(StrictSerializer):
    device_type = serializers.ChoiceField(choices=["NOTEBOOK", "DESKTOP"], required=False)
    cpu = serializers.CharField(max_length=160, required=False, allow_blank=True)
    ram = serializers.CharField(max_length=80, required=False, allow_blank=True)
    ssd = serializers.CharField(max_length=80, required=False, allow_blank=True)
    hdd = serializers.CharField(max_length=80, required=False, allow_blank=True)
    gpu = serializers.CharField(max_length=160, required=False, allow_blank=True)
    os = serializers.CharField(max_length=120, required=False, allow_blank=True)
    os_edition = serializers.CharField(max_length=120, required=False, allow_blank=True)
    size = serializers.CharField(max_length=40, required=False, allow_blank=True)
    purchase_date = serializers.DateField(required=False, allow_null=True)
    note = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    software = AssetSoftwareData(required=False)


class AssetCreate(StrictSerializer):
    organization_id = serializers.UUIDField()
    number = serializers.CharField(max_length=80, required=False, allow_blank=True)
    category = serializers.ChoiceField(choices=["PC", "모니터"], default="PC")
    device_type = serializers.ChoiceField(choices=["NOTEBOOK", "DESKTOP"], default="NOTEBOOK")
    model = serializers.CharField(max_length=160)
    maker = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    serial = serializers.CharField(max_length=160, required=False, allow_blank=True, default="")
    legacy_data = AssetLegacyData(required=False, default=dict)


class AssetUpdate(StrictSerializer):
    expected_version = serializers.IntegerField(min_value=1)
    organization_id = serializers.UUIDField(required=False)
    number = serializers.CharField(max_length=80, required=False, allow_blank=False)
    category = serializers.ChoiceField(choices=["PC", "모니터"], required=False)
    maker = serializers.CharField(max_length=80, required=False, allow_blank=True)
    model = serializers.CharField(max_length=160, required=False)
    serial = serializers.CharField(max_length=160, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Asset._meta.get_field("status").choices, required=False)
    legacy_data = AssetLegacyData(required=False)
