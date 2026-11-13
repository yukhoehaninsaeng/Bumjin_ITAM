import uuid
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("operations", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="MaintenanceContract",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("vendor", models.CharField(max_length=160)),
                ("service", models.CharField(max_length=200)),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("period", models.CharField(blank=True, max_length=120)),
                ("starts_on", models.DateField(blank=True, null=True)),
                ("ends_on", models.DateField(blank=True, null=True)),
                ("costs", models.JSONField(blank=True, default=dict)),
                ("contact", models.CharField(blank=True, max_length=200)),
                ("note", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="maintenance_contracts", to="operations.company")),
                ("organization", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="maintenance_contracts", to="operations.organization")),
            ],
        ),
        migrations.AddIndex(
            model_name="maintenancecontract",
            index=models.Index(fields=["company", "ends_on"], name="maint_company_end"),
        ),
        migrations.AddIndex(
            model_name="maintenancecontract",
            index=models.Index(fields=["active", "ends_on"], name="maint_active_end"),
        ),
    ]
