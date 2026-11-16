import json
from unittest.mock import Mock, patch
from contextlib import contextmanager
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory,force_authenticate
from .models import Company,Organization,Employee,RoleGrant,AuditEvent,SystemConfiguration,SupportTeam
from . import admin_settings as api
from .runtime_settings import display_timezone


@override_settings(ITAM_SETTINGS_KEY=Fernet.generate_key().decode())
class AdminSettingsTests(TestCase):
    def setUp(self):
        User=get_user_model();self.admin=User.objects.create_user(username='admin');self.worker=User.objects.create_user(username='worker')
        self.company=Company.objects.create(code='BJE',name='BJE');self.other=Company.objects.create(code='BJC',name='BJC')
        self.org=Organization.objects.create(company=self.company,name='정보팀')
        self.employee=Employee.objects.create(user=self.worker,company=self.company,organization=self.org,name='사용자',directory_id='ldap-uuid',directory_uid='worker',directory_source='synology')
        self.grant=RoleGrant.objects.create(user=self.admin,role='system_admin',scope_type='GLOBAL')
        self.factory=APIRequestFactory()

    def request(self,view,method='get',data=None,user=None,**kwargs):
        request=getattr(self.factory,method)('/api/v1/admin/settings',data or {},format='json')
        force_authenticate(request,user=user or self.admin)
        return view(request,**kwargs)

    def save(self,group,values,version=0):
        return self.request(api.configuration,'patch',{'values':values,'version':version},group=group)

    def test_forbidden_for_staff_and_company_admin(self):
        self.assertEqual(self.request(api.configuration,user=self.worker,group='mail').status_code,403)
        RoleGrant.objects.create(user=self.worker,role='system_admin',scope_type='COMPANY',company=self.company)
        self.assertEqual(self.request(api.grants,user=self.worker).status_code,403)
        self.assertEqual(self.request(api.connection_test,'post',user=self.worker,group='ldap').status_code,403)

    def test_encrypted_secret_not_returned_or_logged_and_blank_preserved(self):
        r=self.save('mail',{'SMTP_PASSWORD':'secret-value','SMTP_HOST':'smtp.example.test'})
        self.assertEqual(r.status_code,200)
        raw=SystemConfiguration.objects.get(pk='mail')
        self.assertNotIn('secret-value',raw.secrets);self.assertNotIn('secret-value',json.dumps(r.data))
        self.assertNotIn('secret-value',json.dumps(list(AuditEvent.objects.values('data'))))
        self.assertTrue(r.data['data']['secret_configured']['SMTP_PASSWORD'])
        self.assertFalse(r.data['data']['secret_configured']['IMAP_PASSWORD'])
        from .ticket_mail import config
        self.assertEqual(config('SMTP_PASSWORD'),'secret-value')
        self.save('mail',{'SMTP_PASSWORD':''},version=1)
        self.assertEqual(config('SMTP_PASSWORD'),'secret-value')

    @override_settings(ITAM_SETTINGS_KEY='')
    def test_missing_key_rejects_password_save(self):
        self.assertEqual(self.save('ldap',{'LDAP_BIND_PASSWORD':'test'}).status_code,400)
        self.assertFalse(SystemConfiguration.objects.filter(pk='ldap').exists())

    def test_stale_version_and_invalid_values(self):
        self.assertEqual(self.save('ldap',{'LDAP_PORT':'636'}).status_code,200)
        self.assertEqual(self.save('ldap',{'LDAP_PORT':'389'},version=0).status_code,400)
        self.assertEqual(self.save('mail',{'SMTP_PORT':'0'}).status_code,400)
        self.assertEqual(self.save('mail',{'SMTP_HOST':'https://host/path'}).status_code,400)
        self.assertEqual(self.save('system',{'TIME_ZONE':'Bad/Zone'}).status_code,400)

    def test_db_ldap_configuration_used(self):
        self.save('ldap',{'LDAP_HOST':'nas.example.test','LDAP_PORT':'636','LDAP_BIND_PASSWORD':'secret'})
        from .ldap_department import config
        self.assertEqual(config('LDAP_HOST'),'nas.example.test');self.assertEqual(config('LDAP_BIND_PASSWORD'),'secret')

    def test_timezone_preserves_utc_logs(self):
        self.save('system',{'TIME_ZONE':'Asia/Ho_Chi_Minh'})
        self.assertEqual(display_timezone(),'Asia/Ho_Chi_Minh')
        self.assertEqual(AuditEvent.objects.get().created_at.utcoffset().total_seconds(),0)
        self.assertIn('Asia/Seoul',self.request(api.configuration,group='system').data['data']['timezones'])

    def test_grant_roles_and_company_scope_audit(self):
        r=self.request(api.grants,'post',{'employee_id':str(self.employee.id),'role':'asset_manager','scope_type':'COMPANY','company_id':str(self.company.id)})
        self.assertEqual(r.status_code,200)
        self.assertTrue(RoleGrant.objects.filter(user=self.worker,role='asset_manager',company=self.company).exists())
        self.assertEqual(AuditEvent.objects.get().action,'permissions.granted')
        self.assertEqual(self.request(api.directory_accounts).data['data'][0]['uid'],'worker')

    def test_support_role_requires_team_and_adds_membership(self):
        payload={'employee_id':str(self.employee.id),'role':'support_agent','scope_type':'COMPANY','company_id':str(self.company.id)}
        self.assertEqual(self.request(api.grants,'post',payload).status_code,400)
        team=SupportTeam.objects.create(name='IT');team.companies.add(self.company);payload['team_id']=str(team.id)
        self.assertEqual(self.request(api.grants,'post',payload).status_code,200)
        self.assertTrue(team.members.filter(pk=self.worker.pk).exists())

    def test_no_ldap_or_retired_cannot_get_grants(self):
        self.employee.active=False;self.employee.save()
        payload={'employee_id':str(self.employee.id),'role':'system_admin','scope_type':'GLOBAL'}
        self.assertEqual(self.request(api.grants,'post',payload).status_code,400)
        self.employee.active=True;self.employee.directory_id='';self.employee.save()
        self.assertEqual(self.request(api.grants,'post',payload).status_code,400)

    def test_self_global_admin_revocation_prevented(self):
        self.assertEqual(self.request(api.grants,'delete',{'grant_id':self.grant.id}).status_code,400)
        self.assertTrue(RoleGrant.objects.filter(pk=self.grant.id).exists())

    def test_grant_revoke_and_invalid_ids(self):
        grant=RoleGrant.objects.create(user=self.worker,role='organization_manager',scope_type='GLOBAL')
        self.assertEqual(self.request(api.grants,'delete',{'grant_id':grant.id}).status_code,200)
        self.assertFalse(RoleGrant.objects.filter(pk=grant.id).exists())
        self.assertEqual(self.request(api.grants,'post',{'employee_id':'bad'}).status_code,400)
        self.assertEqual(self.request(api.grants,'delete',{'grant_id':'bad'}).status_code,400)

    def test_smtp_test_connects_without_sending(self):
        connection=Mock()
        @contextmanager
        def connected():yield connection
        with patch('operations.ticket_mail.smtp',connected):r=self.request(api.connection_test,'post',group='mail')
        self.assertTrue(r.data['data']['ok']);connection.open.assert_called_once();connection.send_messages.assert_not_called()

    def test_test_failure_does_not_leak_credentials(self):
        with patch('operations.ticket_mail.smtp',side_effect=ValueError('PASSWORD-SECRET')):
            r=self.request(api.connection_test,'post',group='mail')
        self.assertFalse(r.data['data']['ok']);self.assertNotIn('PASSWORD-SECRET',json.dumps(r.data))
        self.assertNotIn('PASSWORD-SECRET',json.dumps(list(AuditEvent.objects.values('data'))))

    def test_history_only_returns_settings_permission_events(self):
        AuditEvent.objects.create(action='ticket.message_added',data={'private':'not-settings'})
        self.save('system',{'TIME_ZONE':'Asia/Seoul'})
        data=self.request(api.history).data
        self.assertEqual(data['meta']['total'],1);self.assertNotIn('private',json.dumps(data))

    def test_middleware_activates_selected_timezone(self):
        from .middleware import ActiveEmployeeMiddleware
        from django.http import HttpResponse
        self.save('system',{'TIME_ZONE':'Asia/Tokyo'})
        request=self.factory.get('/admin');request.user=self.admin
        seen=[]
        def response(request):seen.append(timezone.get_current_timezone_name());return HttpResponse('ok')
        r=ActiveEmployeeMiddleware(response)(request)
        self.assertEqual(seen,['Asia/Tokyo']);self.assertEqual(r['Cache-Control'],'private, no-store')


    def test_ldap_test_reads_base_without_modifying(self):
        conn=Mock();conn.result={'result':0}
        @contextmanager
        def connected():yield conn
        with patch('operations.ldap_department.connection',connected):r=self.request(api.connection_test,'post',group='ldap')
        self.assertTrue(r.data['data']['ok']);conn.search.assert_called_once();conn.modify.assert_not_called()

    def test_key_initialization_and_bootstrap_recovery(self):
        import tempfile
        from pathlib import Path
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO
        with tempfile.TemporaryDirectory() as folder,override_settings(BASE_DIR=Path(folder)):
            output=StringIO();call_command('init_settings_key',stdout=output)
            key=(Path(folder)/'.settings.key').read_text().strip();Fernet(key.encode())
            self.assertNotIn(key,output.getvalue())
            with self.assertRaises(CommandError):call_command('init_settings_key',stdout=output)
        with self.assertRaises(CommandError):call_command('bootstrap_system_admin',self.worker.username,stdout=StringIO())
        self.admin.is_superuser=True;self.admin.save();self.grant.delete()
        call_command('bootstrap_system_admin',self.admin.username,stdout=StringIO())
        self.assertTrue(RoleGrant.objects.filter(user=self.admin,role='system_admin',scope_type='GLOBAL').exists())


    def test_due_date_input_uses_active_timezone(self):
        from rest_framework.fields import DateTimeField
        from zoneinfo import ZoneInfo
        field=DateTimeField()
        with timezone.override(ZoneInfo('Asia/Seoul')):
            value=field.run_validation('2026-11-16T09:00')
            self.assertEqual(value.utcoffset().total_seconds(),9*3600)
            self.assertEqual(value.astimezone(ZoneInfo('UTC')).hour,0)
