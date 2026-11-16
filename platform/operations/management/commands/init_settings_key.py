import os
from pathlib import Path
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help='Create a local encryption key once without printing it. Keep this file with encrypted DB backups.'
    def handle(self,*args,**options):
        path=Path(settings.BASE_DIR)/'.settings.key'
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as handle:handle.write(Fernet.generate_key().decode()+'\n')
        except FileExistsError:raise CommandError('암호화 키 파일이 이미 있습니다. 기존 키를 유지하세요.')
        self.stdout.write('서버 암호화 키 파일을 생성했습니다. ADMIN_SETTINGS.md의 설정 한 줄을 추가하세요.')
