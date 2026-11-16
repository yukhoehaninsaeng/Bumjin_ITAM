from datetime import timedelta
from email.message import EmailMessage
from unittest.mock import patch, Mock
from django.contrib.auth import get_user_model
from django.core.mail import get_connection
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from .models import Company, Organization, Employee, RoleGrant, SupportTeam, Ticket, TicketSequence, TicketMessage, TicketMail
from .ticket_mail import enqueue, dispatch, build, receive, new_text, poll
from .services import claim_ticket, add_ticket_message, transition_ticket, create_ticket

CONFIG={'ENABLED':'1','INBOUND_ENABLED':'1','PUBLIC_BASE_URL':'https://itam.example.test',
        'FROM_EMAIL':'support@example.test','REPLY_ADDRESS':'support@example.test','AUTH_SERV_ID':'mx.example.test'}


@override_settings(ITAM_MAIL=CONFIG)
class TicketMailTests(TestCase):
    def setUp(self):
        User=get_user_model()
        self.requester=User.objects.create_user(username='requester',email='requester@example.test')
        self.agent=User.objects.create_user(username='agent',email='agent@example.test')
        self.company=Company.objects.create(code='BJE',name='범진')
        self.org=Organization.objects.create(company=self.company,name='정보팀')
        self.employee=Employee.objects.create(company=self.company,organization=self.org,user=self.requester,name='요청자',email=self.requester.email)
        RoleGrant.objects.create(user=self.agent,role='support_agent',scope_type='COMPANY',company=self.company)
        self.team=SupportTeam.objects.create(name='IT지원');self.team.companies.add(self.company);self.team.members.add(self.agent)
        self.ticket=Ticket.objects.create(sequence=TicketSequence.objects.create(),company=self.company,requester=self.employee,
            created_by=self.requester,team=self.team,title='<script>문의</script>',body='문의',requester_snapshot={})

    def route(self):
        return TicketMail.objects.create(ticket=self.ticket,recipient=self.requester,address=self.requester.email,portal=True,
            kind='reply',state='sent',sent_at=timezone.now(),message_id='<'+'a'*64+'@example.test>')

    def raw(self,route,body='확인했습니다.',mid='reply-1',sender=None,auth=True,auto=False):
        msg=EmailMessage();msg['From']=sender or route.address;msg['To']='support@example.test';msg['Subject']='Re: 문의'
        msg['Message-ID']=f'<{mid}@example.test>';msg['In-Reply-To']=route.message_id
        if auth:msg['Authentication-Results']='mx.example.test; dmarc=pass header.from=example.test'
        if auto:msg['Auto-Submitted']='auto-replied'
        msg.set_content(body);return msg.as_bytes()

    def test_assignment_and_repeat_claim(self):
        t=claim_ticket(self.agent,self.ticket.id,1)
        self.assertEqual(TicketMail.objects.count(),2)
        claim_ticket(self.agent,self.ticket.id,t.version);self.assertEqual(TicketMail.objects.count(),2)

    def test_public_and_internal_notifications(self):
        add_ticket_message(self.agent,self.ticket.id,'공개 답변')
        add_ticket_message(self.agent,self.ticket.id,'내부 비밀',internal=True)
        self.assertEqual(TicketMail.objects.count(),1)
        msg=build(TicketMail.objects.get());self.assertNotIn('내부 비밀',msg.body)
        self.assertIn('/portal/requests?ticket='+str(self.ticket.id),msg.body)
        self.assertIn('내 문의 바로가기',msg.alternatives[0].content);self.assertNotIn('<script>',msg.alternatives[0].content)

    def test_outbox_rolls_back(self):
        try:
            with transaction.atomic():
                add_ticket_message(self.agent,self.ticket.id,'rollback');raise ValueError()
        except ValueError:pass
        self.assertFalse(TicketMessage.objects.exists());self.assertFalse(TicketMail.objects.exists())

    def test_smtp_retry_success(self):
        enqueue(self.ticket,'created')
        with patch('operations.ticket_mail.smtp',side_effect=OSError('secret')):self.assertEqual(dispatch()['failed'],2)
        self.assertEqual(TicketMail.objects.filter(error_code='OSError',state='pending').count(),2)
        TicketMail.objects.update(available_at=timezone.now())
        with patch('operations.ticket_mail.smtp',side_effect=lambda:get_connection('django.core.mail.backends.locmem.EmailBackend')):self.assertEqual(dispatch()['sent'],2)

    def test_revoked_agent_skipped(self):
        enqueue(self.ticket,'created');RoleGrant.objects.all().delete()
        with patch('operations.ticket_mail.smtp',side_effect=lambda:get_connection('django.core.mail.backends.locmem.EmailBackend')):
            self.assertEqual(dispatch()['skipped'],1)

    def test_received_reply_and_duplicate(self):
        route=self.route();raw=self.raw(route,'새 답변\n--- ITAM 원본 알림 ---\n과거')
        self.assertEqual(receive(raw),'registered');self.assertEqual(receive(raw),'duplicate')
        item=TicketMessage.objects.get();self.assertEqual(item.body,'새 답변');self.assertFalse(item.internal)
        self.assertEqual(TicketMail.objects.filter(state='pending',recipient=self.agent).count(),1)

    def test_wrong_sender_and_auth(self):
        route=self.route()
        self.assertEqual(receive(self.raw(route,sender='stranger@example.test')),'unknown_reply')
        self.assertEqual(receive(self.raw(route,auth=False,mid='unauth')),'sender_not_authenticated')
        self.assertFalse(TicketMessage.objects.exists())

    def test_automatic_empty(self):
        route=self.route();self.assertEqual(receive(self.raw(route,auto=True)),'automatic')
        self.assertEqual(receive(self.raw(route,body='> old',mid='empty')),'invalid_body')

    def test_expired_closed_inactive(self):
        route=self.route();self.ticket.status='CLOSED';self.ticket.save()
        self.assertEqual(receive(self.raw(route)),'ticket_closed')
        self.ticket.status='IN_PROGRESS';self.ticket.save();route.sent_at=timezone.now()-timedelta(days=91);route.save()
        self.assertEqual(receive(self.raw(route,mid='expired')),'expired_reply')
        route.sent_at=timezone.now();route.save();self.requester.is_active=False;self.requester.save()
        self.assertEqual(receive(self.raw(route,mid='inactive')),'access_revoked')

    def test_html_sanitized(self):
        msg=EmailMessage();msg.set_content('<p>확인</p><script>bad()</script><blockquote>과거</blockquote>',subtype='html')
        self.assertEqual(new_text(msg),'확인')

    def test_status_notification(self):
        self.ticket.assignee=self.agent;self.ticket.status='IN_PROGRESS';self.ticket.save()
        transition_ticket(self.agent,self.ticket.id,1,'RESOLVED','완료',portal=False)
        self.assertEqual(TicketMail.objects.get().kind,'status')

    @override_settings(ITAM_MAIL={})
    def test_default_disabled(self):
        enqueue(self.ticket,'created');self.assertFalse(TicketMail.objects.exists());self.assertEqual(dispatch(),{'disabled':True})

    def test_creation(self):
        import uuid
        create_ticket(self.requester,{'title':'생성','body':'문의','type':'QUESTION','category':'OTHER'},str(uuid.uuid4()),portal=True)
        self.assertEqual(TicketMail.objects.filter(kind='created').count(),2)

    def test_gateway_alignment(self):
        route=self.route();raw=self.raw(route).replace(b'mx.example.test;',b'attacker.test;')
        self.assertEqual(receive(raw),'sender_not_authenticated')
        raw=self.raw(route,mid='alignment').replace(b'header.from=example.test',b'header.from=evil.test')
        self.assertEqual(receive(raw),'sender_not_authenticated')

    def test_ack_after_processing(self):
        conn=Mock();conn.select.return_value=('OK',[])
        conn.uid.side_effect=[('OK',[b'42']),('OK',[(b'data',b'mail')]),('OK',[])]
        with override_settings(ITAM_MAIL={**CONFIG,'IMAP_HOST':'imap.test','IMAP_USER':'support','IMAP_PASSWORD':'test'}),patch('operations.ticket_mail.imaplib.IMAP4_SSL',return_value=conn),patch('operations.ticket_mail.receive',return_value='registered'):
            self.assertEqual(poll(),{'registered':1})
        self.assertEqual(conn.uid.call_args.args,('store',b'42','+FLAGS.SILENT','(\\Seen)'))

    def test_no_ack_on_database_failure(self):
        conn=Mock();conn.select.return_value=('OK',[]);conn.uid.side_effect=[('OK',[b'42']),('OK',[(b'data',b'mail')])]
        with override_settings(ITAM_MAIL={**CONFIG,'IMAP_HOST':'imap.test','IMAP_USER':'support','IMAP_PASSWORD':'test'}),patch('operations.ticket_mail.imaplib.IMAP4_SSL',return_value=conn),patch('operations.ticket_mail.receive',side_effect=RuntimeError()):
            with self.assertRaises(RuntimeError):poll()
        self.assertFalse(any(c.args[0]=='store' for c in conn.uid.call_args_list))

    def test_email_import(self):
        from .groupware_sync import link_ldap_entries
        self.employee.groupware_uid='requester';self.employee.save()
        entry={'uid':'requester','id':'stable','mail':'updated@example.test'}
        self.assertEqual(link_ldap_entries([entry],sync_email=True)['email_updates'],1)
        link_ldap_entries([entry],sync_email=True,apply=True);self.employee.refresh_from_db()
        self.assertEqual(self.employee.email,'updated@example.test');self.assertEqual(self.employee.organization,self.org)

    def test_size_limits(self):
        self.assertEqual(receive(b'x'*2097153),'too_large');route=self.route()
        self.assertEqual(receive(self.raw(route,'x'*10001)),'invalid_body')

    def test_fifth_failure(self):
        enqueue(self.ticket,'created');TicketMail.objects.update(attempts=4)
        with patch('operations.ticket_mail.smtp',side_effect=OSError()):dispatch()
        self.assertEqual(TicketMail.objects.filter(state='failed').count(),2)

    def test_attachment_notice(self):
        from email.parser import BytesParser
        from email import policy
        route=self.route();msg=BytesParser(policy=policy.default).parsebytes(self.raw(route))
        msg.add_attachment(b'test',maintype='application',subtype='octet-stream',filename='test.bin')
        self.assertEqual(receive(msg.as_bytes()),'registered');self.assertIn('첨부는 등록되지',TicketMessage.objects.get().body)
