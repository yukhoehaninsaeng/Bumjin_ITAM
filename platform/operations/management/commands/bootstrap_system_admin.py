from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from operations.models import RoleGrant, AuditEvent, Employee


class Command(BaseCommand):
    help='Server-console recovery: grant GLOBAL system_admin to an existing active Django superuser.'
    def add_arguments(self,parser):parser.add_argument('username')
    def handle(self,*args,**options):
        user=get_user_model().objects.filter(username=options['username'],is_active=True,is_superuser=True).first()
        if not user or Employee.objects.filter(user=user,active=False).exists():raise CommandError('재직/활성 상태의 기존 Django superuser만 초기 관리자 권한을 받을 수 있습니다.')
        _,created=RoleGrant.objects.get_or_create(user=user,role='system_admin',scope_type='GLOBAL',company=None)
        if created:AuditEvent.objects.create(actor=user,action='permissions.bootstrap',data={'user_id':user.pk})
        self.stdout.write('시스템 관리자 권한을 확인했습니다.')
