import json
import time
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone
from operations.models import TicketMail, InboundTicketMail
from operations.ticket_mail import dispatch, poll


class Command(BaseCommand):
    help='Process opt-in SMTP outbox and dedicated IMAPS reply inbox.'
    def add_arguments(self,parser):
        parser.add_argument('--watch',action='store_true')
        parser.add_argument('--interval',type=int,default=60)
        parser.add_argument('--limit',type=int,default=50)
        parser.add_argument('--status',action='store_true')
        parser.add_argument('--retry-failed',action='store_true')
    def handle(self,*args,**options):
        if not 1<=options['limit']<=100 or options['interval']<10:raise CommandError('limit 1~100, interval 10초 이상')
        if options['status']:
            from django.db.models import Count
            self.stdout.write(json.dumps({'outbound':list(TicketMail.objects.values('state','error_code').annotate(count=Count('pk'))),
                'inbound':list(InboundTicketMail.objects.values('outcome').annotate(count=Count('pk')))},ensure_ascii=False));return
        if options['retry_failed']:TicketMail.objects.filter(state='failed').update(state='pending',attempts=0,available_at=timezone.now())
        try:
            while True:
                close_old_connections()
                self.stdout.write(json.dumps({'inbound':poll(options['limit']),'outbound':dispatch(options['limit'])},ensure_ascii=False))
                if not options['watch']:break
                time.sleep(options['interval'])
        except KeyboardInterrupt:self.stdout.write('메일 처리를 종료했습니다.')
