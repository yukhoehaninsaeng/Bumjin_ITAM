import csv,hashlib,json
from datetime import date
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.db.models import Q
from operations.models import Employee,DirectoryLease,AuditEvent,VpnApproval


class Command(BaseCommand):
    help='Preview-first legacy VPN CSV/history/document-ID import. Never connects to LDAP or sends mail.'
    def add_arguments(self,parser):
        parser.add_argument('--leases',required=True)
        parser.add_argument('--history')
        parser.add_argument('--processed')
        parser.add_argument('--excluded')
        parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        try:
            with Path(options['leases']).open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
            plan=[];seen=set();unmapped=0
            for row in rows:
                uid=row['username'].strip();group=row['group_name'].strip();start=date.fromisoformat(row['start_date'])
                permanent=row['expire_date']=='9999-12-31';end=None if permanent else date.fromisoformat(row['expire_date'])
                if not uid or len(uid)>128 or not group or len(group)>120 or (end and end<start) or (uid,group) in seen:raise ValueError('invalid/duplicate lease row')
                seen.add((uid,group))
                people=list(Employee.objects.filter(Q(directory_uid=uid)|Q(groupware_uid=uid)).distinct()[:2])
                if len(people)>1:raise ValueError('ambiguous uid mapping')
                employee=people[0] if people else None
                if not employee or not employee.directory_id:unmapped+=1
                plan.append({'uid':uid,'group':group,'starts_on':start,'expires_on':end,'permanent':permanent,'employee':employee,
                    'directory_id':employee.directory_id if employee else '', 'description':str(row.get('description',''))[:500]})
            history=[]
            if options['history']:
                with Path(options['history']).open(encoding='utf-8-sig',newline='') as f:
                    for row in csv.DictReader(f):
                        clean={k:str(row.get(k,''))[:500] for k in ('action_date','uid','name','group','start_date','expire_date','description','action')}
                        clean['source_hash']=hashlib.sha256(json.dumps(clean,sort_keys=True,ensure_ascii=False).encode()).hexdigest();history.append(clean)
            ids={}
            for name,status in [('processed','legacy_processed'),('excluded','excluded')]:
                if options[name]:
                    data=json.loads(Path(options[name]).read_text())
                    if not isinstance(data,list):raise ValueError('document IDs must be a list')
                    for value in data:
                        key=str(value)
                        if not key or len(key)>120:raise ValueError('invalid document ID')
                        ids[key]=status
            report={'mode':'apply' if options['apply'] else 'preview','leases':len(plan),'permanent':sum(p['permanent'] for p in plan),
                'unmapped_or_unlinked':unmapped,'history':len(history),'document_ids':len(ids),'created':0,'existing':0}
            if options['apply']:
                with transaction.atomic():
                    for item in plan:
                        uid=item.pop('uid');group=item.pop('group')
                        _,created=DirectoryLease.objects.get_or_create(uid=uid,group=group,defaults={**item,'source':'legacy'})
                        report['created']+=created;report['existing']+=not created
                    for data in history:
                        if not AuditEvent.objects.filter(action='directory.legacy',data__source_hash=data['source_hash']).exists():AuditEvent.objects.create(action='directory.legacy',data=data)
                    for key,status in ids.items():VpnApproval.objects.get_or_create(pk=key,defaults={'status':status,'reason':'기존 시스템 처리 기록'})
            self.stdout.write(json.dumps(report,ensure_ascii=False,indent=2))
        except (OSError,ValueError,KeyError,csv.Error) as error:raise CommandError('이관 파일 형식/날짜/계정 매핑을 확인하세요. 실제 LDAP는 변경하지 않았습니다.') from error
