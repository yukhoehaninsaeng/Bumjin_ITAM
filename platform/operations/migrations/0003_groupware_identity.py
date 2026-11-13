from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("operations", "0002_maintenancecontract")]
    operations = [
        migrations.AddField(model_name="organization", name="external_source", field=models.CharField(max_length=80, blank=True, default=""), preserve_default=False),
        migrations.AddField(model_name="organization", name="external_code", field=models.CharField(max_length=120, blank=True, default=""), preserve_default=False),
        migrations.AddField(model_name="organization", name="external_data", field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name="employee", name="groupware_uid", field=models.CharField(max_length=128, blank=True, default=""), preserve_default=False),
        migrations.AddField(model_name="employee", name="groupware_data", field=models.JSONField(default=dict, blank=True)),
        migrations.AddConstraint(model_name="organization", constraint=models.UniqueConstraint(fields=["external_source", "external_code"], condition=~Q(external_code=""), name="org_external_identity")),
        migrations.AddConstraint(model_name="employee", constraint=models.UniqueConstraint(fields=["groupware_uid"], condition=~Q(groupware_uid=""), name="employee_groupware_identity")),
    ]
