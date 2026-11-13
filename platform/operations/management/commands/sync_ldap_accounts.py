import json,os,ssl
from django.core.management.base import BaseCommand,CommandError
from operations.groupware_sync import link_ldap_entries


class Command(BaseCommand):
    help='Read LDAP identities and link only exact groupware account IDs. Never reads/writes department attributes.'
    def add_arguments(self,parser):parser.add_argument('--apply',action='store_true')
    def handle(self,*args,**options):
        import ldap3
        connection=None
        try:
            attr=os.environ.get('LDAP_ID_ATTRIBUTE','entryUUID')
            if not attr.replace('-','').isalnum():raise ValueError('LDAP_ID_ATTRIBUTE 형식 오류')
            tls=ldap3.Tls(validate=ssl.CERT_REQUIRED,ca_certs_file=os.environ.get('LDAP_CA_FILE') or None)
            server=ldap3.Server(os.environ['LDAP_HOST'],port=int(os.environ.get('LDAP_PORT','636')),use_ssl=True,tls=tls,connect_timeout=10)
            connection=ldap3.Connection(server,user=os.environ['LDAP_BIND_DN'],password=os.environ['LDAP_BIND_PASSWORD'],auto_bind=True,raise_exceptions=True,receive_timeout=30)
            entries=[]
            for row in connection.extend.standard.paged_search(os.environ['LDAP_BASE_DN'],os.environ.get('LDAP_USER_FILTER','(objectClass=posixAccount)'),attributes=['uid',attr],paged_size=200,generator=True):
                if row.get('type')!='searchResEntry':continue
                def scalar(value):return value[0] if isinstance(value,list) and len(value)==1 else value if not isinstance(value,list) else ''
                entries.append({'uid':scalar(row['attributes'].get('uid','')),'id':scalar(row['attributes'].get(attr,''))})
            result=link_ldap_entries(entries,source=os.environ.get('LDAP_DIRECTORY_SOURCE','synology'),apply=options['apply'])
            result['mode']='apply' if options['apply'] else 'preview'
        except (KeyError,ValueError,OSError,ldap3.core.exceptions.LDAPException) as error:
            raise CommandError('LDAP 조회 또는 계정 매핑에 실패했습니다. 설정·인증서·uid와 고유 ID 중복을 확인하세요.') from error
        finally:
            if connection:connection.unbind()
        self.stdout.write(json.dumps(result,ensure_ascii=False,indent=2))
