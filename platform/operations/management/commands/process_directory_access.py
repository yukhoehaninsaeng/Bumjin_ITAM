import json,time
from django.core.management.base import BaseCommand,CommandError
from django.db import close_old_connections
from operations.directory_access import process_leases,notify


class Command(BaseCommand):
    help='Preview/apply VPN start/expiry membership and send queued access notices. LDAP writes require both --apply and configured write switch.'
    def add_arguments(self,parser):
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--watch',action='store_true')
        parser.add_argument('--interval',type=int,default=300)
    def handle(self,*args,**options):
        if options['interval']<60:raise CommandError('interval은 60초 이상입니다.')
        try:
            while True:
                close_old_connections()
                result={'leases':process_leases(options['apply'])}
                if options['apply']:result['mail']=notify()
                self.stdout.write(json.dumps(result,ensure_ascii=False))
                if not options['watch']:break
                time.sleep(options['interval'])
        except KeyboardInterrupt:self.stdout.write('VPN 처리를 종료했습니다.')
