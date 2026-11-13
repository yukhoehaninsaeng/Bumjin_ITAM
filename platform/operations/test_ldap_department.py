from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch, Mock
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.db import transaction
from .models import Company, Organization, Employee, RoleGrant
from .ldap_department import save_department, sync_employee, write_department, DepartmentSyncError, sync_state
from .test_groupware_sync import GroupwareMappingTests
from .groupware_sync import apply_groupware


class LdapDepartmentTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(code='BJE',name='BJE')
        self.org=Organization.objects.create(company=self.company,name='정보팀')
        self.other=Organization.objects.create(company=self.company,name='개발팀')
        self.user=get_user_model().objects.create_user(username='manager')
        RoleGrant.objects.create(user=self.user,role='organization_manager',scope_type='COMPANY',company=self.company)
        self.employee=Employee.objects.create(company=self.company,organization=self.org,name='테스트',directory_uid='test',directory_id='stable-id',directory_source='synology')

    def save(self):
        with transaction.atomic():
            save_department(self.employee,self.other,self.user,self.employee.version)

    def test_failure_preserves_intent_and_retry_succeeds(self):
        self.save()
        with patch('operations.ldap_department.write_department',side_effect=DepartmentSyncError('연결 실패')):
            employee,state=sync_employee(self.employee.id,self.user)
        self.assertEqual(state['status'],'failed');self.assertEqual(employee.organization,self.other)
        with patch('operations.ldap_department.write_department',return_value='departmentNumber'):
            employee,state=sync_employee(self.employee.id,self.user)
        self.assertEqual(state['status'],'synced')
        self.other.name='개발지원팀';self.other.save();employee.refresh_from_db()
        self.assertEqual(sync_state(employee)['status'],'pending')

    def test_stale_edit_and_retired_rejected(self):
        with self.assertRaises(DepartmentSyncError):save_department(self.employee,self.other,self.user,0)
        self.employee.active=False
        with self.assertRaises(DepartmentSyncError):self.save()

    def test_out_of_scope_retry_never_contacts_ldap(self):
        other_user=get_user_model().objects.create_user(username='no-grant')
        with patch('operations.ldap_department.write_department') as writer:
            with self.assertRaises(Employee.DoesNotExist):sync_employee(self.employee.id,other_user)
            writer.assert_not_called()

    def test_groupware_import_preserves_manual_department_and_retirement(self):
        plan=GroupwareMappingTests().plan();apply_groupware(plan)
        self.employee=Employee.objects.get(groupware_uid='worker');self.save()
        apply_groupware(plan);self.employee.refresh_from_db()
        self.assertEqual(self.employee.organization,self.other)
        self.assertIn('manual_department',self.employee.groupware_data)
        plan['members'][0].update(active=False,department='')
        apply_groupware(plan);self.employee.refresh_from_db()
        self.assertFalse(self.employee.active)
        self.assertEqual(self.employee.organization,self.other)

    @override_settings(ITAM_LDAP={'LDAP_DEPARTMENT_WRITE_ENABLED':'1','LDAP_DEPARTMENT_ATTRIBUTE':'departmentNumber'})
    def test_only_department_is_written_and_read_back(self):
        conn=Mock();conn.modify.return_value=True
        @contextmanager
        def connected():yield conn
        before={'dn':'uid=test,cn=users,dc=example','attributes':{'uid':['test'],'entryUUID':['stable-id'],'departmentNumber':['옛부서']}}
        after=deepcopy(before);after['attributes']['departmentNumber']=['정보팀']
        with patch('operations.ldap_department.connection',connected),patch('operations.ldap_department.lookup',side_effect=[before,after]):
            self.assertEqual(write_department(self.employee),'departmentNumber')
        args=conn.modify.call_args.args
        self.assertEqual(args[0],before['dn']);self.assertEqual(list(args[1]),['departmentNumber'])

    @override_settings(ITAM_LDAP={'LDAP_DEPARTMENT_WRITE_ENABLED':'1','LDAP_DEPARTMENT_ATTRIBUTE':'departmentNumber'})
    def test_recreated_account_is_never_written(self):
        conn=Mock()
        @contextmanager
        def connected():yield conn
        row={'dn':'test','attributes':{'uid':['test'],'entryUUID':['new-id']}}
        with patch('operations.ldap_department.connection',connected),patch('operations.ldap_department.lookup',return_value=row):
            with self.assertRaises(DepartmentSyncError):write_department(self.employee)
        conn.modify.assert_not_called()

    @override_settings(ITAM_LDAP={'LDAP_DEPARTMENT_WRITE_ENABLED':'1','LDAP_DEPARTMENT_ATTRIBUTE':'userPassword'})
    def test_security_attributes_rejected(self):
        with self.assertRaises(DepartmentSyncError):write_department(self.employee)

    @override_settings(ITAM_LDAP={'LDAP_DEPARTMENT_WRITE_ENABLED':'0'})
    def test_disabled_write_does_not_connect(self):
        with patch('operations.ldap_department.connection') as connect:
            with self.assertRaises(DepartmentSyncError):write_department(self.employee)
            connect.assert_not_called()

    def request_department(self, user, org, version=None):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from .api import employee_department
        request=APIRequestFactory().post('/department', {'organization_id':str(org.id),
            'expected_version':version if version is not None else self.employee.version},format='json')
        force_authenticate(request,user=user)
        return employee_department(request,employee_id=self.employee.id)

    def test_api_checks_both_source_and_destination_scope(self):
        company=Company.objects.create(code='BJC',name='BJC')
        org=Organization.objects.create(company=company,name='타법인')
        with patch('operations.ldap_department.write_department') as writer:
            self.assertEqual(self.request_department(self.user,org).status_code,404)
            no_grant=get_user_model().objects.create_user(username='no-scope')
            self.assertEqual(self.request_department(no_grant,self.other).status_code,404)
            writer.assert_not_called()
        self.employee.refresh_from_db();self.assertEqual(self.employee.organization,self.org)

    def test_api_returns_saved_department_with_failure_and_version_conflict(self):
        with patch('operations.ldap_department.write_department',side_effect=DepartmentSyncError('오프라인')):
            response=self.request_department(self.user,self.other)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['data']['ldap_department']['status'],'failed')
        self.assertEqual(self.request_department(self.user,self.org,version=1).status_code,400)
        self.employee.refresh_from_db();self.assertEqual(self.employee.organization,self.other)
