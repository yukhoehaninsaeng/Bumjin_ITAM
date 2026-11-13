import json
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from operations.groupware_sync import load_groupware, summarize, apply_groupware


class Command(BaseCommand):
    help='Sync departments and employment status from groupware XLSX; preview by default.'
    def add_arguments(self,parser):
        parser.add_argument('--departments',required=True)
        parser.add_argument('--members',required=True)
        parser.add_argument('--company-map',help='Optional JSON of root department code to company code.')
        parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        try:
            mapping=json.loads(Path(options['company_map']).read_text(encoding='utf-8-sig')) if options.get('company_map') else None
            if mapping is not None and (not isinstance(mapping,dict) or not all(isinstance(k,str) and isinstance(v,str) and v for k,v in mapping.items())):
                raise ValueError('법인 매핑은 부서코드: 법인코드 JSON 객체여야 합니다.')
            plan=load_groupware(options['departments'],options['members'],mapping)
            result=apply_groupware(plan) if options['apply'] else summarize(plan)
        except (ValueError,OSError,ValidationError,IntegrityError) as error:
            raise CommandError(str(error)) from error
        result['mode']='apply' if options['apply'] else 'preview'
        self.stdout.write(json.dumps(result,ensure_ascii=False,indent=2))
