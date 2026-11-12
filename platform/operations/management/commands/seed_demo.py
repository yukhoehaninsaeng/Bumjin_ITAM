import getpass
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from operations.models import (
    Asset,
    AssetAssignment,
    Company,
    Employee,
    Organization,
    RoleGrant,
    SupportTeam,
)
from operations.services import create_ticket
import uuid


class Command(BaseCommand):
    help = "Explicit, development-only demo data; never runs on startup."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("ITAM_DEBUG=1인 개발 환경에서만 실행할 수 있습니다.")
        User = get_user_model()
        if User.objects.filter(username__in=["demo-admin", "demo-employee"]).exists():
            raise CommandError("데모 계정이 이미 있습니다. 중복 실행하지 않습니다.")
        password = getpass.getpass("데모 두 계정에 사용할 비밀번호: ")
        validate_password(password)
        c = Company.objects.create(code="DEMO", name="데모 법인")
        it = Organization.objects.create(company=c, name="IT운영팀")
        support = Organization.objects.create(company=c, name="경영지원팀")
        admin = User.objects.create_user("demo-admin", password=password)
        employee = User.objects.create_user("demo-employee", password=password)
        Employee.objects.create(user=admin, company=c, organization=it, name="김민수")
        person = Employee.objects.create(
            user=employee,
            company=c,
            organization=support,
            name="이서연",
            email="employee@example.com",
        )
        for role in ["support_agent", "asset_manager", "organization_manager", "system_admin"]:
            RoleGrant.objects.create(user=admin, company=c, role=role)
        team = SupportTeam.objects.create(name="IT 지원팀")
        team.companies.add(c)
        team.members.add(admin)
        asset = Asset.objects.create(
            company=c,
            organization=support,
            number="DEMO-N-001",
            maker="Lenovo",
            model="ThinkPad X1 Carbon",
            status="IN_USE",
        )
        AssetAssignment.objects.create(asset=asset, employee=person, organization=support)
        Asset.objects.create(
            company=c,
            organization=it,
            number="DEMO-M-001",
            category="모니터",
            maker="Dell",
            model="UltraSharp",
            status="IN_STOCK",
        )
        create_ticket(
            employee,
            {
                "title": "노트북 화면이 깜빡입니다",
                "body": "업무 중 화면이 깜빡여 확인을 요청드립니다.",
                "type": "INCIDENT",
                "category": "DEVICE",
                "asset_ids": [str(asset.id)],
            },
            uuid.uuid4(),
            portal=True,
        )
        self.stdout.write(
            self.style.SUCCESS("생성 완료: demo-admin / demo-employee (입력한 비밀번호 사용)")
        )
