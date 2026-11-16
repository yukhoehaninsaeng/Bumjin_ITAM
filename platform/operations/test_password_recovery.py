import json
from datetime import timedelta
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.mail import get_connection
from django.test import TestCase,override_settings,RequestFactory,Client
from django.utils import timezone
from .models import Employee,Company,Organization,PasswordRecovery,AccessNotice,AuditEvent
from . import password_recovery as recovery
from .runtime_settings import cipher
from .directory_access import notify


@override_settings(ITAM_SETTINGS_KEY=Fernet.generate_key().decode(),ITAM_ACCESS={'PASSWORD_WRITE_ENABLED':'1','PASSWORD_RECOVERY_ENABLED':'1'},ITAM_MAIL={'ENABLED':'1','FROM_EMAIL':'support@example.test','PUBLIC_BASE_URL':'https://itam.example.test'})
class PasswordRecoveryTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user(username='worker',email='worker@example.test')
        self.company=Company.objects.create(code='BJE',name='BJE');self.org=Organization.objects.create(company=self.company,name='IT')
        self.employee=Employee.objects.create(user=self.user,company=self.company,organization=self.org,name='사용자',email=self.user.email,
            directory_uid='worker',directory_id='ldap-stable',directory_source='synology')
    def requested(self):
        recovery.request_recovery('worker','worker@example.test')
        item=PasswordRecovery.objects.latest('created_at');notice=AccessNotice.objects.filter(kind='password_recovery').latest('created_at')
        token=cipher().decrypt(notice.payload['encrypted_token'].encode()).decode()
        return item,notice,token
    def post(self,values):
        request=RequestFactory().post('/password-recovery/submit',data=json.dumps(values),content_type='application/json',secure=True)
        request.META['REMOTE_ADDR']='127.0.0.1';request._dont_enforce_csrf_checks=True
        return recovery.submit(request)
    def test_request_stores_hash_and_encrypted_delivery_token(self):
        item,notice,token=self.requested();self.assertNotEqual(item.token_hash,token)
        self.assertNotIn(token,json.dumps(notice.payload));self.assertNotIn(token,json.dumps(list(AuditEvent.objects.values('data'))))
        url=recovery.delivery(notice);self.assertTrue(url.startswith('https://itam.example.test/password-recovery#'))
        self.assertNotIn('?token=',url);self.assertIn(token,url)
    def test_unknown_and_wrong_email_have_identical_response(self):
        a=self.post({'action':'request','account':'unknown','email':'unknown@example.test'})
        b=self.post({'action':'request','account':'worker','email':'wrong@example.test'})
        self.assertEqual(a.status_code,b.status_code);self.assertIn('no-store',a['Cache-Control']);self.assertEqual(a.content,b.content);self.assertFalse(PasswordRecovery.objects.exists())
    def test_valid_link_consumed_once(self):
        item,notice,token=self.requested()
        with patch('operations.directory_access.password_operation') as operation:
            recovery.finish(item.pk,token,'New-password-2026!')
            self.assertEqual(operation.call_args.args[0].pk,self.employee.pk)
            with self.assertRaises(ValueError):recovery.finish(item.pk,token,'Another-password-2026!')
            self.assertEqual(operation.call_count,1)
    def test_new_request_invalidates_older_link(self):
        first,_,token=self.requested();second,_,newtoken=self.requested()
        with patch('operations.directory_access.password_operation') as operation:
            with self.assertRaises(ValueError):recovery.finish(first.pk,token,'New-password-2026!')
            recovery.finish(second.pk,newtoken,'New-password-2026!');self.assertEqual(operation.call_count,1)
    def test_expired_and_tampered_link_rejected(self):
        item,_,token=self.requested()
        with patch('operations.directory_access.password_operation') as operation:
            with self.assertRaises(ValueError):recovery.finish(item.pk,'x'*43,'New-password-2026!')
            PasswordRecovery.objects.filter(pk=item.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
            with self.assertRaises(ValueError):recovery.finish(item.pk,token,'New-password-2026!')
            operation.assert_not_called()
    def test_inactive_user_email_and_identity_changes_invalidate(self):
        item,_,token=self.requested();self.employee.email='changed@example.test';self.employee.save()
        with patch('operations.directory_access.password_operation') as operation:
            with self.assertRaises(ValueError):recovery.finish(item.pk,token,'New-password-2026!')
            operation.assert_not_called()
        self.employee.email=self.user.email;self.employee.directory_id='new-id';self.employee.save()
        with self.assertRaises(ValueError):recovery.finish(item.pk,token,'New-password-2026!')
    def test_intervening_password_change_invalidates(self):
        item,_,token=self.requested();self.user.set_unusable_password();self.user.save()
        with self.assertRaises(ValueError):recovery.finish(item.pk,token,'New-password-2026!')
    def test_failed_nas_attempt_still_consumes_link(self):
        item,_,token=self.requested()
        with patch('operations.directory_access.password_operation',side_effect=OSError('private')):
            with self.assertRaises(ValueError):recovery.finish(item.pk,token,'New-password-2026!')
        item.refresh_from_db();self.assertIsNotNone(item.used_at)
        self.assertNotIn('private',json.dumps(list(AuditEvent.objects.values('data'))))
    def test_local_admin_cannot_request_directory_recovery(self):
        get_user_model().objects.create_user(username='itamadmin',email='admin@example.test')
        recovery.request_recovery('itamadmin','admin@example.test');self.assertFalse(PasswordRecovery.objects.exists())
        self.user.is_superuser=True;self.user.save();recovery.request_recovery('worker','worker@example.test');self.assertFalse(PasswordRecovery.objects.exists())
    def test_three_requests_per_half_hour(self):
        for _ in range(5):recovery.request_recovery('worker','worker@example.test')
        self.assertEqual(PasswordRecovery.objects.count(),3)
    def test_mail_is_one_time_link_and_delivery_token_removed(self):
        item,notice,token=self.requested()
        with patch('operations.ticket_mail.smtp',side_effect=lambda:get_connection('django.core.mail.backends.locmem.EmailBackend')):
            self.assertEqual(notify()['sent'],1)
        from django.core import mail
        self.assertIn(token,mail.outbox[-1].body);self.assertIn('비밀번호 재설정',mail.outbox[-1].alternatives[0].content)
        notice.refresh_from_db();self.assertNotIn('encrypted_token',notice.payload)
    @override_settings(ITAM_ACCESS={'PASSWORD_WRITE_ENABLED':'1','PASSWORD_RECOVERY_ENABLED':'0'})
    def test_disabled_by_default(self):
        recovery.request_recovery('worker','worker@example.test');self.assertFalse(PasswordRecovery.objects.exists())
    @override_settings(ROOT_URLCONF='operations.test_password_recovery')
    def test_anonymous_post_requires_csrf(self):
        client=Client(enforce_csrf_checks=True)
        response=client.post('/password-recovery/submit',data=json.dumps({'account':'worker','email':self.user.email}),content_type='application/json')
        self.assertEqual(response.status_code,403)
    def test_complete_response_never_returns_password_or_token(self):
        item,_,token=self.requested()
        with patch('operations.directory_access.password_operation'):
            response=self.post({'action':'complete','id':str(item.pk),'token':token,'password':'New-password-2026!','confirm_password':'New-password-2026!'})
        self.assertEqual(response.status_code,200);self.assertIn('no-store',response['Cache-Control']);self.assertNotIn(token,response.content.decode());self.assertNotIn('New-password',response.content.decode())


from django.urls import path
urlpatterns=[path('password-recovery/submit',recovery.submit)]
