"""DB overrides with encrypted secrets; absence preserves existing deployment settings."""
import json
import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.conf import settings
from django.db import OperationalError, ProgrammingError
from .models import SystemConfiguration


def encryption_key():
    return getattr(settings,'ITAM_SETTINGS_KEY','') or os.environ.get('ITAM_SETTINGS_KEY','')


def cipher():
    from cryptography.fernet import Fernet
    key=encryption_key()
    if not key:raise ValueError('서버에 ITAM_SETTINGS_KEY를 먼저 설정하세요.')
    try:return Fernet(key.encode() if isinstance(key,str) else key)
    except (ValueError,TypeError):raise ValueError('ITAM_SETTINGS_KEY 형식을 확인하세요.')


def decrypt(value):
    if not value:return {}
    from cryptography.fernet import InvalidToken
    try:return json.loads(cipher().decrypt(value.encode()).decode())
    except (InvalidToken,ValueError,TypeError):raise ValueError('저장한 연결 비밀번호를 복호화할 수 없습니다. 서버 암호화 키를 확인하세요.')


def row(group):
    # Before initial migration, retain deployment defaults. Do not create rows while reading.
    try:return SystemConfiguration.objects.filter(pk=group).first()
    except (OperationalError,ProgrammingError):return None


def override(group,key,default):
    item=row(group)
    if not item:return default
    if key in item.values:return item.values[key]
    if key.endswith('PASSWORD'):return decrypt(item.secrets).get(key,default)
    return default


def display_timezone():
    name=str(override('system','TIME_ZONE',settings.TIME_ZONE))
    try:ZoneInfo(name)
    except (ZoneInfoNotFoundError,ValueError):return 'Asia/Seoul'
    return name
