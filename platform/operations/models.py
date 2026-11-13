import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.code


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    name = models.CharField(max_length=80)
    version = models.PositiveIntegerField(default=1)
    external_source = models.CharField(max_length=80, blank=True)
    external_code = models.CharField(max_length=120, blank=True)
    external_data = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["external_source", "external_code"],
                condition=~Q(external_code=""),
                name="org_external_identity",
            ),
            models.UniqueConstraint(
                fields=["company", "parent", "name"],
                condition=Q(parent__isnull=False),
                name="org_unique_child",
            ),
            models.UniqueConstraint(
                fields=["company", "name"], condition=Q(parent__isnull=True), name="org_unique_root"
            ),
        ]

    def __str__(self):
        return self.name


class Employee(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employee",
    )
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    name = models.CharField(max_length=80)
    email = models.EmailField(blank=True)
    active = models.BooleanField(default=True)
    directory_source = models.CharField(max_length=80, blank=True)
    directory_id = models.CharField(max_length=255, blank=True)
    directory_uid = models.CharField(max_length=128, blank=True)
    groupware_uid = models.CharField(max_length=128, blank=True)
    groupware_data = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["directory_source", "directory_id"],
                condition=~Q(directory_id=""),
                name="employee_directory_identity",
            ),
            models.UniqueConstraint(
                fields=["groupware_uid"], condition=~Q(groupware_uid=""),
                name="employee_groupware_identity",
            )
        ]

    def __str__(self):
        return self.name


class RoleGrant(models.Model):
    ROLES = [
        (x, x) for x in ["support_agent", "asset_manager", "organization_manager", "system_admin"]
    ]
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="role_grants"
    )
    role = models.CharField(max_length=32, choices=ROLES)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.PROTECT)
    scope_type = models.CharField(
        max_length=10, choices=[("COMPANY", "법인"), ("GLOBAL", "전체")], default="COMPANY"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(scope_type="COMPANY", company__isnull=False)
                    | Q(scope_type="GLOBAL", company__isnull=True)
                ),
                name="grant_explicit_scope",
            )
        ]


class SupportTeam(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    active = models.BooleanField(default=True)
    companies = models.ManyToManyField(Company)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL)

    def __str__(self):
        return self.name


class Asset(models.Model):
    STATES = [
        ("IN_USE", "사용"),
        ("IN_STOCK", "보관"),
        ("REPAIR", "수리"),
        ("LOST", "분실"),
        ("DISPOSED", "폐기"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    number = models.CharField(max_length=80)
    category = models.CharField(max_length=32, default="PC")
    maker = models.CharField(max_length=80, blank=True)
    model = models.CharField(max_length=160)
    serial = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=16, choices=STATES, default="IN_STOCK")
    version = models.PositiveIntegerField(default=1)
    legacy_data = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="asset_company_number")
        ]
        indexes = [models.Index(fields=["company", "status"])]


class AssetAssignment(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="assignments")
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    assigned_at = models.DateTimeField(default=timezone.now)
    returned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["asset"],
                condition=Q(returned_at__isnull=True),
                name="asset_one_active_assignment",
            ),
            models.CheckConstraint(
                condition=Q(returned_at__isnull=True) | Q(returned_at__gte=models.F("assigned_at")),
                name="assignment_dates_ordered",
            ),
        ]


class MaintenanceContract(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="maintenance_contracts")
    organization = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.PROTECT, related_name="maintenance_contracts"
    )
    vendor = models.CharField(max_length=160)
    service = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(default=1)
    period = models.CharField(max_length=120, blank=True)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    costs = models.JSONField(default=dict, blank=True)
    contact = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["company", "ends_on"], name="maint_company_end"),
            models.Index(fields=["active", "ends_on"], name="maint_active_end"),
        ]


class TicketSequence(models.Model):
    """Database-generated ticket numbers; gaps are intentional and never reused."""

    pass


class Ticket(models.Model):
    STATES = [
        ("NEW", "접수"),
        ("IN_PROGRESS", "처리 중"),
        ("WAITING_REQUESTER", "요청자 답변 대기"),
        ("WAITING_VENDOR", "외부 업체 대기"),
        ("RESOLVED", "해결"),
        ("CLOSED", "종료"),
        ("CANCELLED", "취소"),
    ]
    PRIORITIES = [("URGENT", "긴급"), ("HIGH", "높음"), ("NORMAL", "보통"), ("LOW", "낮음")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # A DB-generated identity, never MAX()+1. UUID is the stable API identifier.
    sequence = models.OneToOneField(TicketSequence, on_delete=models.PROTECT)
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    requester = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="requested_tickets"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_tickets"
    )
    requester_snapshot = models.JSONField(default=dict)
    title = models.CharField(max_length=200)
    body = models.TextField()
    type = models.CharField(
        max_length=20,
        choices=[("INCIDENT", "장애"), ("REQUEST", "서비스 요청"), ("QUESTION", "문의")],
        default="INCIDENT",
    )
    category = models.CharField(
        max_length=30,
        choices=[
            ("DEVICE", "PC·주변기기"),
            ("ACCOUNT", "계정·권한"),
            ("SOFTWARE", "소프트웨어"),
            ("NETWORK", "네트워크"),
            ("OTHER", "기타"),
        ],
        default="DEVICE",
    )
    status = models.CharField(max_length=24, choices=STATES, default="NEW")
    priority = models.CharField(max_length=10, choices=PRIORITIES, default="NORMAL")
    team = models.ForeignKey(SupportTeam, on_delete=models.PROTECT)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_tickets",
    )
    assets = models.ManyToManyField(Asset, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["company", "status", "-created_at"]),
            models.Index(fields=["assignee", "status"]),
            models.Index(fields=["requester", "-created_at"]),
            models.Index(
                fields=["due_at"],
                condition=Q(
                    status__in=["NEW", "IN_PROGRESS", "WAITING_REQUESTER", "WAITING_VENDOR"]
                ),
                name="ticket_open_due",
            ),
        ]

    @property
    def number(self):
        return f"TK-{self.sequence_id:06d}"


class TicketMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name="messages")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField()
    internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]


class UploadDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    expires_at = models.DateTimeField()
    ticket = models.OneToOneField(Ticket, null=True, blank=True, on_delete=models.PROTECT)


def attachment_path(instance, filename):
    return f"attachments/{instance.id.hex[:2]}/{instance.id.hex}"


class Attachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    draft = models.ForeignKey(UploadDraft, on_delete=models.PROTECT, related_name="attachments")
    ticket = models.ForeignKey(
        Ticket, null=True, blank=True, on_delete=models.PROTECT, related_name="attachments"
    )
    file = models.FileField(upload_to=attachment_path, max_length=200)
    name = models.CharField(max_length=255)
    media_type = models.CharField(max_length=120)
    size = models.PositiveIntegerField()
    checksum = models.CharField(max_length=64)
    is_image = models.BooleanField(default=False)


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    company = models.ForeignKey(Company, null=True, on_delete=models.PROTECT)
    ticket = models.ForeignKey(Ticket, null=True, on_delete=models.PROTECT, related_name="events")
    action = models.CharField(max_length=60)
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]


class IdempotencyRecord(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.UUIDField()
    payload_hash = models.CharField(max_length=64)
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], name="ticket_idempotency_user_key")
        ]


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()

