from contextlib import contextmanager
from datetime import date,timedelta
from email.message import EmailMessage
from io import StringIO
import json,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase,override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory,force_authenticate
from .models import Company,Organization,Employee,RoleGrant,DirectoryLease,AccessNotice,AuditEvent,VpnApproval
from . import directory_access as access,directory_api as api
from .vpn_groupware import period


@override_settings(ITAM_ACCESS={'VPN_WRITE_ENABLED':'1','PASSWORD_WRITE_ENABLED':'1'},ITAM_LDAP={'LDAP_DIRECTORY_SOURCE':'synology','LDAP_ID_ATTRIBUTE':'entryUUID','LDAP_BASE_DN':'dc=test'})
class DirectoryAccessTests(TestCase):
    def setUp(self):
        User=get_user_model();self.admin=User.objects.create_user(username='admin-web');self.user=User.objects.create_user(username='worker')
        self.company=Company.objects.create(code='BJE',name='BJE');self.org=Organization.objects.create(company=self.company,name='정보팀')
        self.employee=Employee.objects.create(company=self.company,organization=self.org,name='사용자',user=self.user,directory_uid='worker',directory_id='id-1',directory_source='synology',email='worker@example.test')
        RoleGrant.objects.create(user=self.admin,role='system_admin',scope_type='GLOBAL')
        self.factory=APIRequestFactory()
    def request(self,view,method='get',data=None,user=None,**kwargs):
        request=getattr(self.factory,method)('/api',data or {},format='json',secure=True);request.META['REMOTE_ADDR']='127.0.0.1'
        force_authenticate(request,user=user or self.admin);return view(request,**kwargs)
    def lease(self,**kwargs):
        defaults={'employee':self.employee,'uid':'worker','directory_id':'id-1','group':'01. VPN_User_Period','starts_on':access.local_today(),'expires_on':access.local_today()+timedelta(days=5)};defaults.update(kwargs)
        return DirectoryLease.objects.create(**defaults)
    def test_boundaries_and_permanent(self):
        l=self.lease(starts_on=date(2026,11,1),expires_on=date(2026,11,16))
        self.assertTrue(access.desired_membership(l,date(2026,11,16)));self.assertFalse(access.desired_membership(l,date(2026,11,17)))
        self.assertFalse(access.desired_membership(l,date(2026,10,31)));l.permanent=True;l.expires_on=None
        self.assertTrue(access.desired_membership(l,date(2030,1,1)));l.enabled=False;self.assertFalse(access.desired_membership(l,date(2030,1,1)))
    def test_save_and_version_conflict(self):
        payload={'employee_id':str(self.employee.id),'group':'01. VPN_User_Period','starts_on':str(access.local_today()),'expires_on':str(access.local_today()+timedelta(days=7)),'permanent':False}
        r=self.request(api.leases,'post',payload);self.assertEqual(r.status_code,201)
        self.assertEqual(self.request(api.leases,'post',payload).status_code,400)
        payload['expected_version']=r.data['data']['version'];self.assertEqual(self.request(api.leases,'post',payload).status_code,201)
    def test_unauthorized_users_blocked(self):
        self.assertEqual(self.request(api.leases,user=self.user).status_code,403)
        self.assertEqual(self.request(api.reset_password,'post',user=self.user,employee_id=self.employee.pk).status_code,403)
    def test_preview_never_touches_ldap(self):
        self.lease()
        with patch('operations.directory_access.connection') as conn:
            self.assertEqual(access.process_leases(False)['changes_needed'],1);conn.assert_not_called()
    def test_sync_failure_keeps_retryable_lease(self):
        l=self.lease()
        with patch('operations.directory_access.connection',side_effect=OSError()):result=access.apply_lease(l.pk,self.admin)
        self.assertEqual(result.sync_state,'failed');self.assertTrue(result.enabled)
        self.assertEqual(AuditEvent.objects.get().data['status'],'failed')
    def test_ldap_write_switch_off(self):
        l=self.lease()
        with override_settings(ITAM_ACCESS={'VPN_WRITE_ENABLED':'0'}),patch('operations.directory_access.connection') as conn:
            self.assertEqual(access.apply_lease(l.pk).error_code,'vpn_write_disabled');conn.assert_not_called()
    def test_immutable_identity_prevents_writes(self):
        conn=Mock();row={'dn':'uid=worker,cn=users,dc=test','attributes':{'uid':['worker'],'entryUUID':['recreated']}}
        with patch('operations.directory_access.lookup',return_value=row):
            with self.assertRaises(ValueError):access.membership(conn,self.employee,'01. VPN_User_Period','id-1',True)
        conn.modify.assert_not_called()
    def test_allowlisted_group_and_write_readback(self):
        conn=Mock();conn.modify.return_value=True
        user={'dn':'uid=worker,cn=users,dc=test','attributes':{'uid':['worker'],'entryUUID':['id-1']}}
        before={'dn':'cn=VPN,cn=groups,dc=test','attributes':{'member':[]}};after={'dn':before['dn'],'attributes':{'member':[user['dn']]}}
        with patch('operations.directory_access.lookup',return_value=user),patch('operations.directory_access.group_entry',side_effect=[(before,'member'),(after,'member')]):
            self.assertTrue(access.membership(conn,self.employee,'01. VPN_User_Period','id-1',True))
        args=conn.modify.call_args.args;self.assertEqual(list(args[1]),['member'])
        with self.assertRaises(ValueError):access.group_entry(conn,'administrators')
    def test_expiry_only_removes_group_not_account(self):
        l=self.lease(expires_on=access.local_today()-timedelta(days=1),starts_on=access.local_today()-timedelta(days=8))
        @contextmanager
        def connected():yield Mock()
        with patch('operations.directory_access.connection',connected),patch('operations.directory_access.membership',return_value=False) as member:
            result=access.apply_lease(l.pk)
        self.assertFalse(member.call_args.kwargs['desired']);self.assertFalse(result.observed_member)
        self.assertEqual(result.sync_state,'synced')
    def test_retired_user_revocation_keeps_identity_checks(self):
        self.employee.active=False;self.employee.save();conn=Mock()
        row={'dn':'uid=worker,cn=users,dc=test','attributes':{'uid':['worker'],'entryUUID':['id-1']}}
        group={'dn':'cn=VPN,cn=groups,dc=test','attributes':{'member':[]}}
        with patch('operations.directory_access.lookup',return_value=row),patch('operations.directory_access.group_entry',return_value=(group,'member')):
            self.assertFalse(access.membership(conn,self.employee,'01. VPN_User_Period','id-1',False))
            with self.assertRaises(ValueError):access.membership(conn,self.employee,'01. VPN_User_Period','id-1',True)
    def test_warning_queued_once(self):
        l=self.lease(sync_state='synced',observed_member=True)
        access.process_leases(True);access.process_leases(True)
        self.assertEqual(AccessNotice.objects.filter(kind='vpn_expiry').count(),1)
    def test_password_response_one_time_and_no_audit_secret(self):
        with patch('operations.directory_api.confirm_operator'),patch('operations.directory_access.password_operation') as operation:
            r=self.request(api.reset_password,'post',{'reason':'본인 확인 후 초기화'},employee_id=self.employee.id)
        self.assertEqual(r.status_code,200);self.assertEqual(len(r.data['data']['temporary_password']),20)
        self.assertEqual(r['Cache-Control'],'no-store')
        self.assertNotIn(r.data['data']['temporary_password'],json.dumps(list(AuditEvent.objects.values('data'))))
        self.assertEqual(operation.call_args.args[0].pk,self.employee.pk)
    def test_password_reset_requires_reconfirmation(self):
        with patch('operations.directory_access.password_operation') as operation:
            self.assertEqual(self.request(api.reset_password,'post',{'reason':'test'},employee_id=self.employee.id).status_code,400)
            operation.assert_not_called()
    def test_password_policy_and_protected_accounts(self):
        with self.assertRaises(ValueError):access.password_operation(self.employee,self.admin,'short')
        self.employee.directory_uid='root'
        with self.assertRaises(ValueError):access.verified(Mock(),self.employee)
    def test_password_write_targets_only_userpassword(self):
        conn=Mock();conn.modify.return_value=True
        @contextmanager
        def connected():yield conn
        row={'dn':'uid=worker,cn=users,dc=test','attributes':{'uid':['worker'],'entryUUID':['id-1']}}
        old=self.user.password
        with patch('operations.directory_access.connection',connected),patch('operations.directory_access.lookup',return_value=row):
            access.password_operation(self.employee,self.admin,'A-strong-New-password-2026!')
        self.assertEqual(list(conn.modify.call_args.args[1]),['userPassword'])
        self.user.refresh_from_db();self.assertNotEqual(old,self.user.password);self.assertFalse(self.user.has_usable_password())
        self.assertEqual(AccessNotice.objects.get().kind,'password_reset')
        self.assertNotIn('A-strong',json.dumps(list(AuditEvent.objects.values('data'))))
    def test_groupware_period_never_falls_back_to_auto_grant(self):
        self.assertEqual(period({'document':{'docBodyContent':'날짜 없음'}})[:2],(None,None))
        start,end,reason=period({'document':{'docBodyContent':'요청 기간 2026-11-16 ~ 2026-11-20'}})
        self.assertEqual((start,end),(date(2026,11,16),date(2026,11,20)));self.assertEqual(reason,'')
        self.assertIsNone(period({'document':{'docBodyContent':'요청 기간 2026-11-20 ~ 2026-11-16'}})[0])
    def test_approval_requires_explicit_account_review_and_deduplicates(self):
        item=VpnApproval.objects.create(document_id='document-1',starts_on=access.local_today(),expires_on=access.local_today()+timedelta(days=5),drafter='사용자')
        payload={'action':'approve','document_id':item.pk,'expected_version':item.version,'employee_id':str(self.employee.pk)}
        self.assertEqual(self.request(api.approvals,'post',payload).status_code,200)
        self.assertEqual(DirectoryLease.objects.get().uid,'worker')
        self.assertEqual(self.request(api.approvals,'post',payload).status_code,400)
    def test_approval_does_not_bridge_disjoint_periods(self):
        self.lease(starts_on=date(2026,1,1),expires_on=date(2026,1,5))
        item=VpnApproval.objects.create(document_id='document-1',starts_on=date(2026,2,1),expires_on=date(2026,2,5))
        payload={'action':'approve','document_id':item.pk,'expected_version':1,'employee_id':str(self.employee.pk)}
        self.assertEqual(self.request(api.approvals,'post',payload).status_code,400)
    def test_legacy_preview_apply_idempotent_and_unmapped_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            file=Path(folder)/'leases.csv';file.write_text('username,group_name,start_date,expire_date,description\nworker,01. VPN_User_Period,2026-11-01,2026-11-30,test\nunknown,00. VPN_User_Always,2026-01-01,9999-12-31,test\n',encoding='utf-8-sig')
            with patch('operations.directory_access.connection') as conn:
                out=StringIO();call_command('import_directory_legacy',leases=str(file),stdout=out);self.assertEqual(json.loads(out.getvalue())['unmapped_or_unlinked'],1);self.assertFalse(DirectoryLease.objects.exists())
                call_command('import_directory_legacy',leases=str(file),apply=True,stdout=StringIO());call_command('import_directory_legacy',leases=str(file),apply=True,stdout=StringIO());conn.assert_not_called()
            self.assertEqual(DirectoryLease.objects.count(),2);self.assertIsNone(DirectoryLease.objects.get(uid='unknown').employee_id)
    def test_user_portal_exposes_only_own_leases(self):
        self.lease();r=self.request(api.my_account,user=self.user)
        self.assertEqual(len(r.data['data']['leases']),1)
        self.assertEqual(self.request(api.my_account,user=self.admin).status_code,403)

    def test_self_password_change_targets_logged_in_user(self):
        with patch('operations.directory_access.password_operation') as operation,patch('operations.directory_api.update_session_auth_hash'):
            r=self.request(api.my_account,'post',{'current_password':'old','new_password':'New-password-2026!','confirm_password':'New-password-2026!','employee_id':str(self.admin.pk)},user=self.user)
        self.assertEqual(r.status_code,200);self.assertEqual(operation.call_args.args[0].pk,self.employee.pk)
        self.assertEqual(operation.call_args.kwargs['current'],'old')
    def test_incorrect_current_password_never_modifies(self):
        conn=Mock()
        @contextmanager
        def connected():yield conn
        with patch('operations.directory_access.connection',connected),patch('operations.directory_access.verified',return_value={'dn':'uid=worker,dc=test'}),patch('ldap3.Connection',side_effect=ValueError('invalid')):
            with self.assertRaises(ValueError):access.password_operation(self.employee,self.user,'New-password-2026!',current='wrong')
        conn.modify.assert_not_called()
    def test_groupware_fetch_only_queues_review(self):
        from .vpn_groupware import fetch_approvals
        docs=[{'id':'1','formName':'VPN 신청','apprStatus':'APPROVAL','docStatus':'COMPLETE','drafterName':'사용자','docNum':'DOC-1'}]
        detail={'document':{'docBodyContent':'요청 기간 2026-11-16 ~ 2026-11-20'}}
        with patch('operations.vpn_groupware.login',return_value='test-cookie'),patch('operations.vpn_groupware.api',side_effect=[docs,detail]):
            self.assertEqual(fetch_approvals(self.admin)['new'],1)
        self.assertEqual(VpnApproval.objects.get().status,'review');self.assertFalse(DirectoryLease.objects.exists())
    def test_access_secrets_encrypted_and_not_returned(self):
        from cryptography.fernet import Fernet
        from .admin_settings import configuration
        from .models import SystemConfiguration
        with override_settings(ITAM_SETTINGS_KEY=Fernet.generate_key().decode()):
            response=self.request(configuration,'patch',{'version':0,'values':{'GW_COOKIE':'private-session','GW_PASSWORD':'private-password'}},group='access')
            self.assertEqual(response.status_code,200)
            self.assertNotIn('private-session',json.dumps(response.data));self.assertNotIn('private-password',SystemConfiguration.objects.get(pk='access').secrets)
            self.assertEqual(access.config('GW_COOKIE'),'private-session');self.assertEqual(access.config('GW_PASSWORD'),'private-password')
    def test_notice_contains_no_password_and_uses_smtp(self):
        from django.core.mail import get_connection
        from django.core import mail
        access.queue_notice(self.employee,'password_reset','test-reset')
        with override_settings(ITAM_MAIL={'ENABLED':'1','FROM_EMAIL':'support@example.test'}),patch('operations.ticket_mail.smtp',side_effect=lambda:get_connection('django.core.mail.backends.locmem.EmailBackend')):
            self.assertEqual(access.notify()['sent'],1)
        self.assertIn('비밀번호는 메일로 전송하지 않습니다',mail.outbox[-1].body)
    def test_inactive_login_never_receives_vpn_grant(self):
        self.user.is_active=False;self.user.save();self.employee.refresh_from_db()
        lease=self.lease();lease.employee=self.employee
        self.assertFalse(access.desired_membership(lease))
        with self.assertRaises(ValueError):access.verified(Mock(),self.employee)
