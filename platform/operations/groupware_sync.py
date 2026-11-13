"""Groupware owns membership; LDAP owns immutable identity and authentication."""
import hashlib
import uuid
from pathlib import Path
import openpyxl
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from .models import Company, Organization, Employee, AuditEvent

NAMESPACE = uuid.UUID('4ddae072-62b2-4c35-939b-eb336253b1af')
ROOT_COMPANIES = {
    'BJC': 'BJC', 'BJM52': 'BJM', 'IM_SUP': 'BJM', 'BJIS42': 'BJIS',
    'BJV': 'BJV', 'HBC': 'HBC', 'IPOC': 'IPOC', 'BEI': 'BEI',
    'BJAM': 'BJAM', 'HBJ': 'HBJ',
}
COMPANY_NAMES = {'BJE':'범진전자', 'BJC':'시엔엘', 'BJM':'아이엔디(금형)', 'BJIS':'아이엔디(성형)',
                 'BJV':'베트남법인', 'HBC':'혜주법인', 'IPOC':'범진동관IPO', 'BEI':'인니법인',
                 'BJAM':'멕시코법인', 'HBJ':'헝가리법인'}


def text(value):
    return str(value or '').strip()


def stable_id(kind, key):
    return uuid.uuid5(NAMESPACE, kind + ':' + key)


def read_table(path, required):
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != '.xlsx':
        raise ValueError('XLSX 파일 경로를 확인하세요.')
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in book:
            sheet.reset_dimensions()  # Some groupware exports declare an incorrect A1-only range.
            rows = sheet.iter_rows(values_only=True)
            header = [text(v) for v in next(rows, ())]
            if not set(required).issubset(header):
                continue
            result = []
            for number, row in enumerate(rows, 2):
                if not any(value is not None and text(value) for value in row):
                    continue
                result.append({'row':number, **{key:text(row[i]) if i<len(row) else '' for i,key in enumerate(header) if key}})
            return result, checksum
    finally:
        book.close()
    raise ValueError('필수 Excel 헤더가 없습니다: ' + ', '.join(required))


def load_groupware(departments_path, members_path, company_map=None):
    departments, department_hash = read_table(departments_path, ['부서코드','상위 부서코드','부서명 (한글)'])
    members, member_hash = read_table(members_path, ['아이디','이름 (한글)','부서코드'])
    mapping = dict(ROOT_COMPANIES)
    mapping.update(company_map or {})
    result = {}; real_codes = set()
    for row in departments:
        real_code = row['부서코드']; parent = row['상위 부서코드']; name = row['부서명 (한글)']
        if not name:
            raise ValueError(f'부서명 누락: 행 {row["row"]}')
        code = real_code or ('display:' + hashlib.sha256((parent+'|'+name).encode()).hexdigest()[:32])
        if code in result:
            raise ValueError('중복 부서코드: ' + code)
        result[code] = {'code':code,'name':name,'parent_code':parent,'row':row['row'],
                        'display_only':not bool(real_code),'sort_code':row.get('정렬코드','')}
        if real_code: real_codes.add(real_code)
    for node in result.values():
        chain=set(); current=node
        while current['parent_code']:
            if current['code'] in chain:
                raise ValueError('조직 상하위 순환: '+node['code'])
            chain.add(current['code'])
            if current['parent_code'] not in result:
                raise ValueError('존재하지 않는 상위 부서코드: '+current['parent_code'])
            current=result[current['parent_code']]
        # Headquarters and domestic electronics roots default to BJE by explicit operator choice.
        node['company']=mapping.get(current['code'], 'BJE')
    people=[]; seen=set()
    for row in members:
        account=row['아이디']; department=row['부서코드']
        if not account or len(account)>128 or account in seen:
            raise ValueError(f'아이디 누락/중복/길이 초과: 행 {row["row"]}')
        seen.add(account)
        if department and department not in real_codes:
            raise ValueError('사용자의 부서코드를 찾을 수 없습니다: '+department)
        if not row['이름 (한글)'] or len(row['이름 (한글)'])>80:
            raise ValueError(f'사용자 이름 누락/길이 초과: 행 {row["row"]}')
        people.append({'uid':account,'name':row['이름 (한글)'],'department':department,
                       'active':bool(department),'row':row['row'],
                       'job':{key:row.get(key,'') for key in ['사번','직위','직급','직책','멤버타입','겸직 부서 및 직책']}})
    return {'departments':result,'members':people,'department_hash':department_hash,'member_hash':member_hash}


def find_employee(account):
    # Never match by display name/email, and never select arbitrarily among duplicated old mappings.
    matches=list(Employee.objects.filter(Q(groupware_uid=account)|Q(directory_uid=account)|Q(user__username=account)).distinct()[:2])
    if len(matches)>1:
        raise ValueError('기존 직원 계정 매핑이 중복됩니다. 먼저 정리하세요: '+account)
    return matches[0] if matches else None


def summarize(plan):
    people=plan['members']; conflicts=[]; matched=0
    for person in people:
        try: matched+=bool(find_employee(person['uid']))
        except ValueError as error: conflicts.append(str(error))
    return {'departments':len(plan['departments']),
            'display_only_departments':sum(d['display_only'] for d in plan['departments'].values()),
            'members':len(people),'active':sum(p['active'] for p in people),
            'retired':sum(not p['active'] for p in people),'existing_employee_matches':matched,
            'conflicts':conflicts,'department_checksum':plan['department_hash'],
            'member_checksum':plan['member_hash'],
            'company_departments':{c:sum(d['company']==c for d in plan['departments'].values()) for c in sorted({d['company'] for d in plan['departments'].values()})}}


@transaction.atomic
def apply_groupware(plan):
    report=summarize(plan)
    if report['conflicts']:
        raise ValueError('기존 직원 계정 매핑 중복을 해결한 뒤 적용하세요.')
    companies={}
    for code in {d['company'] for d in plan['departments'].values()}|{'BJE'}:
        companies[code],_=Company.objects.get_or_create(code=code,defaults={'name':COMPANY_NAMES.get(code,code)})
    mapped={}; pending=dict(plan['departments']); org_created=org_updated=0
    while pending:
        progressed=False
        for code,node in list(pending.items()):
            if node['parent_code'] and node['parent_code'] not in mapped: continue
            company=companies[node['company']]; parent=mapped.get(node['parent_code'])
            if parent and parent.company_id != company.id:
                raise ValueError('상위 조직과 법인 매핑이 다릅니다: '+code)
            org=Organization.objects.select_for_update().filter(external_source='groupware',external_code=code).first()
            if org is None:
                # Adopt an exact, unambiguous legacy organization only at the same hierarchy position.
                candidates=list(Organization.objects.filter(company=company,name=node['name'],parent=parent,external_code='')[:2])
                org=candidates[0] if len(candidates)==1 else Organization(id=stable_id('department',code),company=company,name=node['name'])
            created=org._state.adding
            values={'company':company,'parent':parent,'name':node['name'],'external_source':'groupware','external_code':code,
                    'external_data':{'source_row':node['row'],'parent_code':node['parent_code'],'sort_code':node['sort_code'],
                                     'display_only':node['display_only'],'department_checksum':plan['department_hash']}}
            changed=created or any(getattr(org,k)!=v for k,v in values.items())
            if changed:
                for k,v in values.items():setattr(org,k,v)
                if not created:org.version+=1
                org.full_clean();org.save()
                org_created+=created;org_updated+=not created
            mapped[code]=org;del pending[code];progressed=True
        if not progressed:raise ValueError('조직 상하위 구조를 정리할 수 없습니다.')
    created_members=updated_members=disabled_logins=0
    for person in plan['members']:
        employee=find_employee(person['uid']);new=employee is None
        old_data=employee.groupware_data if employee else {}
        if person['active']:
            org=mapped[person['department']];company=org.company
        elif employee:
            org=employee.organization;company=employee.company
        else:
            company=companies['BJE']
            org,_=Organization.objects.get_or_create(company=company,parent=None,name='퇴사자 (부서 미지정)',defaults={'external_data':{'retired_placeholder':True}})
        if employee is None:
            employee=Employee(id=stable_id('member',person['uid']),company=company,organization=org,directory_uid=person['uid'])
        data={'source':'groupware','department_code':person['department'],'source_row':person['row'],
              'member_checksum':plan['member_hash'],'employment_status':'active' if person['active'] else 'retired',
              'job':person['job'],'company_unconfirmed':bool(not person['active'] and (new or old_data.get('company_unconfirmed'))),'disabled_by_groupware':bool(old_data.get('disabled_by_groupware'))}
        user=employee.user if employee.user_id else None
        if not person['active'] and user:
            if user.is_active:
                data['disabled_by_groupware']=True;user.is_active=False;user.save(update_fields=['is_active']);disabled_logins+=1
        elif person['active'] and user and data['disabled_by_groupware']:
            user.is_active=True;user.save(update_fields=['is_active']);data['disabled_by_groupware']=False
        values={'name':person['name'],'active':person['active'],'company':company,'organization':org,
                'groupware_uid':person['uid'],'groupware_data':data}
        changed=new or any(getattr(employee,k)!=v for k,v in values.items())
        if changed:
            for k,v in values.items():setattr(employee,k,v)
            if not new:employee.version+=1
            employee.full_clean();employee.save()
            created_members+=new;updated_members+=not new
            AuditEvent.objects.create(company=company,action='employee.groupware_sync',
                data={'employee_id':str(employee.id),'groupware_uid':person['uid'],'active':person['active'],
                      'department_code':person['department'],'member_checksum':plan['member_hash']})
    return {**report,'organizations_created':org_created,'organizations_updated':org_updated,
            'employees_created':created_members,'employees_updated':updated_members,'logins_disabled':disabled_logins}


@transaction.atomic
def link_ldap_entries(entries, source='synology', apply=False):
    """Read-only LDAP result mapping. Does not change groupware organization or employment status."""
    User=get_user_model();seen=set();identities=set();report={'matched':0,'unmapped':0,'retired':0,'linked':0}
    for entry in entries:
        account=text(entry.get('uid'));identity=text(entry.get('id'))
        if not account or not identity or account in seen or identity in identities:
            raise ValueError('LDAP uid/고유 ID 누락 또는 중복입니다.')
        seen.add(account);identities.add(identity)
        employee=Employee.objects.select_for_update().filter(groupware_uid=account).first()
        if employee is None:report['unmapped']+=1;continue
        if not employee.active:report['retired']+=1;continue
        report['matched']+=1
        if employee.directory_source==source and employee.directory_id and employee.directory_id!=identity:
            raise ValueError('LDAP 고유 ID가 변경되었습니다. 계정 재생성 여부를 확인하세요: '+account)
        if Employee.objects.filter(directory_source=source,directory_id=identity).exclude(pk=employee.pk).exists():
            raise ValueError('LDAP 고유 ID가 다른 직원에게 연결되어 있습니다.')
        if not apply:continue
        user=employee.user if employee.user_id else None
        if user is None:
            if User.objects.filter(username=account).exists():
                raise ValueError('같은 이름의 기존 로그인 계정이 있습니다. 수동 연결이 필요합니다: '+account)
            user=User(username=account,is_active=True);user.set_unusable_password();user.save();employee.user=user
        if user.username != account:
            raise ValueError('기존 로그인 아이디가 그룹웨어 아이디와 다릅니다: '+account)
        employee.directory_source=source;employee.directory_uid=account;employee.directory_id=identity
        employee.version+=1;employee.save()
        report['linked']+=1
    return report
