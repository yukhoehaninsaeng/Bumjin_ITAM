from django.core.management.base import BaseCommand, CommandError
from operations.ldap_department import connection, lookup, values, DepartmentSyncError


class Command(BaseCommand):
    help = 'Read-only: identify the LDAP attribute containing a unique value set in the NAS Department field.'

    def add_arguments(self, parser):
        parser.add_argument('--uid', required=True)
        parser.add_argument('--value', required=True)

    def handle(self, *args, **options):
        if len(options['value']) < 12:
            raise CommandError('테스트 계정의 부서 칸에 입력한 12자 이상 고유 확인 문자열을 지정하세요.')
        try:
            with connection() as conn:
                row = lookup(conn, options['uid'], ['*'])
                matches = [key for key in row['attributes'] if values(row, key) == [options['value']]]
            if len(matches) != 1:
                raise DepartmentSyncError('정확히 일치하는 속성이 하나가 아닙니다. NAS 부서 칸과 확인 문자열을 확인하세요.')
        except (DepartmentSyncError, ValueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write('LDAP_DEPARTMENT_ATTRIBUTE=' + matches[0])
        self.stdout.write('조회만 완료했습니다. 다른 개인정보나 비밀번호는 출력하지 않습니다.')
