from copy import deepcopy
from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import Employee, Organization, Company, Asset, AssetAssignment
from .groupware_sync import apply_groupware, link_ldap_entries


class GroupwareMappingTests(TestCase):
    def plan(self):
        return {'department_hash':'dept-hash','member_hash':'member-hash','departments':{
            'TEAM':{'code':'TEAM','name':'정보팀','parent_code':'','row':2,'display_only':False,'sort_code':'1','company':'BJE'}},
            'members':[{'uid':'worker','name':'사용자','department':'TEAM','active':True,'row':2,'job':{}},
                       {'uid':'retired','name':'퇴사자','department':'','active':False,'row':3,'job':{}}]}

    def test_idempotent_import_and_stable_department_rename(self):
        plan=self.plan();apply_groupware(plan)
        original=Organization.objects.get(external_code='TEAM').id
        result=apply_groupware(plan)
        self.assertEqual(result['employees_created'],0)
        self.assertEqual(result['employees_updated'],0)
        plan['departments']['TEAM']['name']='인사정보팀'
        apply_groupware(plan)
        self.assertEqual(Organization.objects.get(external_code='TEAM').id,original)
        self.assertEqual(Employee.objects.count(),2)

    def test_blank_department_is_retired_without_deleting_the_person(self):
        apply_groupware(self.plan())
        employee=Employee.objects.get(groupware_uid='retired')
        self.assertFalse(employee.active)
        self.assertTrue(employee.groupware_data['company_unconfirmed'])
        self.assertEqual(Employee.objects.filter(active=True).count(),1)

    def test_ldap_identity_does_not_override_department(self):
        apply_groupware(self.plan());employee=Employee.objects.get(groupware_uid='worker');original=employee.organization_id
        link_ldap_entries([{'uid':'worker','id':'stable-directory-id'}],apply=True)
        employee.refresh_from_db()
        self.assertEqual(employee.organization_id,original)
        self.assertFalse(employee.user.has_usable_password())
        self.assertEqual(employee.directory_id,'stable-directory-id')
        with self.assertRaises(ValueError):
            link_ldap_entries([{'uid':'worker','id':'recreated-account-id'}],apply=True)

    def test_retirement_blocks_login_and_return_only_restores_its_own_disable(self):
        plan=self.plan();apply_groupware(plan)
        link_ldap_entries([{'uid':'worker','id':'stable-directory-id'}],apply=True)
        employee=Employee.objects.get(groupware_uid='worker');original=employee.organization_id
        asset=Asset.objects.create(company=employee.company,organization=employee.organization,number='TEST-N-001',model='Test',status='IN_USE')
        assignment=AssetAssignment.objects.create(asset=asset,employee=employee,organization=employee.organization)
        retired=deepcopy(plan);retired['members'][0].update(active=False,department='')
        apply_groupware(retired);employee.refresh_from_db();self.assertFalse(employee.user.is_active)
        self.assertEqual(employee.organization_id,original)
        assignment.refresh_from_db();self.assertIsNone(assignment.returned_at)
        self.assertEqual(assignment.employee_id,employee.id)
        apply_groupware(plan);employee.refresh_from_db();self.assertTrue(employee.user.is_active)
        employee.user.is_active=False;employee.user.save()
        apply_groupware(plan);employee.refresh_from_db();self.assertFalse(employee.user.is_active)

    def test_ambiguous_uid_rolls_back_instead_of_matching_by_name(self):
        plan=self.plan();apply_groupware(plan)
        company=Company.objects.get(code='BJE');org=Organization.objects.get(external_code='TEAM')
        Employee.objects.create(company=company,organization=org,name='동명이인',directory_uid='worker')
        with self.assertRaises(ValueError):apply_groupware(plan)
        self.assertEqual(Employee.objects.count(),3)

    def test_ldap_does_not_create_unmapped_or_retired_accounts(self):
        apply_groupware(self.plan())
        result=link_ldap_entries([{'uid':'retired','id':'uuid-1'},{'uid':'unknown','id':'uuid-2'}],apply=True)
        self.assertEqual(result['retired'],1);self.assertEqual(result['unmapped'],1)
        self.assertFalse(get_user_model().objects.exists())

    def test_orphan_login_must_be_manually_mapped_before_sync(self):
        get_user_model().objects.create_user(username='retired',password='not-a-production-password')
        with self.assertRaises(ValueError):apply_groupware(self.plan())
        self.assertFalse(Employee.objects.exists())
