import getpass
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from operations.models import Company, Employee, Organization, RoleGrant, SupportTeam


class Command(BaseCommand):
    help = "Create an explicitly scoped local operator. Password is entered privately, never a CLI argument."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--company", required=True)
        parser.add_argument("--name", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        if User.objects.filter(username=options["username"]).exists():
            raise CommandError("이미 존재하는 계정입니다. 자동 승격하지 않습니다.")
        company, _ = Company.objects.get_or_create(
            code=options["company"], defaults={"name": options["company"]}
        )
        org, _ = Organization.objects.get_or_create(company=company, parent=None, name="IT운영팀")
        user = User(username=options["username"])
        password = getpass.getpass("관리자 비밀번호 (12자 이상): ")
        if password != getpass.getpass("비밀번호 확인: "):
            raise CommandError("비밀번호가 다릅니다.")
        validate_password(password, user)
        user.set_password(password)
        user.save()
        Employee.objects.create(user=user, company=company, organization=org, name=options["name"])
        for role in ["support_agent", "asset_manager", "organization_manager", "system_admin"]:
            RoleGrant.objects.create(user=user, company=company, role=role)
        team, _ = SupportTeam.objects.get_or_create(name=f"{company.code} IT 지원팀")
        team.companies.add(company)
        team.members.add(user)
        self.stdout.write(
            self.style.SUCCESS(f"{user.username}: {company.code} 범위 운영 계정 생성 완료.")
        )
