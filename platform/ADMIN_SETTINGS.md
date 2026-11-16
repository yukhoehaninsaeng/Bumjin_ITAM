# 관리자 설정 화면

`/admin/settings`에서 사용자 권한, SMTP/IMAP, LDAP, 표시 시간대, 변경 이력을 관리합니다. 이 변경은 메일 기능 PR #9에 이어지는 작업입니다. PR #9를 먼저 병합하고 적용합니다.

## 최초 적용 (서버에서 한 번)

기존 DB 비밀번호가 있는 `config/settings.py`는 유지합니다. 새 코드·의존성·migration을 적용하세요.

```powershell
cd 'C:\Users\BJE\Desktop\AI_PJT\Bumjin_ITAM-main\platform'
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py init_settings_key
```

비밀번호 암호화 키가 `platform\.settings.key`에 생성됩니다. 명령은 키를 화면에 출력하지 않으며 기존 파일을 덮어쓰지 않습니다. 기존 로컬 `config/settings.py` 맨 아래에 다음 한 줄을 추가합니다.

```python
ITAM_SETTINGS_KEY = (BASE_DIR / '.settings.key').read_text(encoding='utf-8').strip()
```

또는 서버 환경변수 ITAM_SETTINGS_KEY로 같은 Fernet 키를 전달할 수 있습니다. 웹 서버와 메일 worker가 같은 키·DB를 사용해야 합니다. 생성된 키를 GitHub에 올리지 마세요(기존 *.key 규칙으로 제외됨). DB 백업과 함께 별도로 보관하고 서버 이전 시 복사해야 합니다. 키를 잃으면 저장된 연결 비밀번호를 복호화할 수 없습니다. 일반 Django SECRET_KEY 변경과는 독립적입니다. Windows에서는 해당 파일을 서비스 실행 계정만 읽도록 ACL을 관리하세요.

최초 관리자에게 전체 시스템 관리자 역할이 없으면 **기존 활성 Django superuser**를 지정합니다.

```powershell
.\.venv\Scripts\python.exe manage.py bootstrap_system_admin 실제슈퍼유저아이디
```

superuser가 없으면 `manage.py createsuperuser`로 먼저 생성합니다. 이 명령은 서버 콘솔에서만 실행하며 LDAP 사용자에게 Django superuser를 부여하지 않습니다. bootstrap 이후 해당 관리자로 로그인하여 LDAP 사용자에게 필요한 역할을 부여하세요.

## 1. LDAP 사용자 권한

사용자 권한 탭에서 이름/LDAP ID를 검색 → 사용자 선택 → 역할 → 법인 또는 전체 범위 → 권한 부여.

| 역할 | 기능 |
| --- | --- |
| 자산 관리자 | 부여된 범위의 자산 관리 |
| 티켓 담당자 | 부여된 범위와 소속 지원팀의 티켓 처리 |
| 조직 관리자 | 부여된 범위의 사용자·조직 조회/부서 변경 |
| 시스템 관리자 | 전체 연결 설정·시간대·권한·설정 이력 관리 |

티켓 담당자는 지원팀을 같이 지정합니다. 선택 법인이 해당 팀에 연결돼 있어야 하며 기존 티켓 접근 검사를 유지합니다. 전체 범위의 티켓 담당자도 지원팀 소속이 필요합니다. 시스템 관리자 역할 자체가 모든 자산·티켓 조회 역할을 자동 추가하지는 않으므로 필요하면 각각 부여하세요.

목록에는 LDAP 계정 연결을 완료한 재직 사용자만 표시합니다. 새 계정은 기존 그룹웨어/LDAP 동기화 절차로 먼저 연결하세요. 미연결 로그인이나 이름이 같은 사람을 자동 인수하지 않습니다. 퇴사자·비활성 계정에는 권한을 추가할 수 없습니다. 기존 권한은 개별 해제할 수 있고 본인의 전체 시스템 관리자 역할 해제는 차단합니다. 권한 변경은 변경자·시각·대상·범위를 기록합니다.

연결 설정은 전사 공통이므로 전체(GLOBAL) system_admin만 접근합니다. 법인(COMPANY) 범위 system_admin은 이 설정 화면에 접근할 수 없습니다.

## 2. SMTP·수신 메일

SMTP·수신 메일 탭에 발송/수신 서버·포트·계정·프로그램 주소를 입력하고 저장합니다. 비밀번호는 입력 후 저장되면 재표시하지 않으며 빈칸은 기존 값을 유지합니다. 다른 비밀번호를 입력하면 교체합니다. 연결 테스트는 **저장한 SMTP 값으로 접속/인증만** 확인하고 메일을 보내지 않습니다. 인증서·DNS·호스트·계정 문제는 연결 실패로 표시합니다.

메일 답장 자동 등록을 켜기 전 전용 IMAP 메일함과 AUTH_SERV_ID 신뢰 조건을 `TICKET_MAIL.md`에서 확인하세요. SMTP 연결 성공만으로 IMAP 수신 인증까지 검증된 것은 아닙니다. 메일 수신/발송을 실제 처리하려면 별도 process_ticket_mail worker를 실행해야 합니다.

```powershell
.\.venv\Scripts\python.exe manage.py process_ticket_mail --watch --interval 60
```

## 3. LDAP 연결

LDAP 탭에서 LDAPS 서버·포트·Base DN·Bind DN·비밀번호·신뢰 CA·고유 ID 속성을 지정합니다. 저장한 값은 LDAP 로그인, 계정 연결 명령, 부서 쓰기에서 공통 사용합니다. 연결 테스트는 bind 및 Base DN 읽기만 확인하고 계정/부서를 수정하지 않습니다. 부서 속성 쓰기 권한은 `GROUPWARE_MAPPING.md`의 테스트 계정 절차로 별도 확인하세요.

필요할 때 서버에서 기존 동기화 명령을 실행합니다.

```powershell
.\.venv\Scripts\python.exe manage.py sync_ldap_accounts --sync-email
.\.venv\Scripts\python.exe manage.py sync_ldap_accounts --sync-email --apply
```

UI에 LDAP 비밀번호를 새로 저장하면 기존 파일/환경변수보다 DB 설정이 우선됩니다. 화면에서 수정하지 않은 항목은 기존 ITAM_LDAP/ITAM_MAIL 환경설정을 유지합니다. 설정을 바꾼 뒤 연결 테스트를 수행하세요. 사용자 이름 매핑 기준/디렉터리 소스·고유 ID 속성을 운영 중에 임의 변경하면 기존 연결과 충돌할 수 있으므로 기존 값과 동일하게 유지합니다.

## 4. 시간대 / 로그

기본은 Asia/Seoul. 시간대 탭에서 IANA 시간대를 선택하여 저장합니다(예: Asia/Ho_Chi_Minh, Asia/Shanghai, UTC).

- 업무 처리 로그 원본과 서버 내부 타임스탬프는 UTC로 유지합니다(USE_TZ=True).
- 화면의 업무 처리 시각·설정 이력·목표일 표시는 선택 시간대로 변환합니다.
- 목표 처리일 datetime-local 입력은 선택 시간대를 기준으로 서버에서 해석합니다. 브라우저 기기 시간대가 달라도 선택 시간대를 사용합니다.
- 기존 로그 원본 시각은 변경하지 않습니다. 날짜만 있는 구입일/계약일은 시간대 변환 대상이 아닙니다.
- 서버 시스템 시계/NTP를 웹에서 수정하지 않습니다. 실제 시계 오차는 OS/NAS NTP로 보정해야 합니다.

저장 후 다른 브라우저 탭은 새로고침해야 새 표시 시간대가 반영됩니다. worker는 DB 설정을 다시 읽고 다음 처리에 반영합니다.

## 보관과 검증

SMTP/LDAP 비밀번호는 Fernet 인증 암호화 후 DB에 저장하며 API·감사 이력에 원문/암호문을 반환하지 않습니다. 설정·권한 변경 이력에는 변경한 항목 이름, 변경자 ID, 서버 시각만 남깁니다. 새 SystemConfiguration 테이블은 PostgreSQL RLS를 활성화합니다. 서버 DB 역할은 테이블 소유자/BYPASSRLS여야 하며 Supabase anon/authenticated 공개 정책을 추가하지 않습니다.

Django API/권한/암호화/시간대 테스트 및 모의 SMTP/IMAP 테스트로 검증합니다. 실제 NAS·SMTP 서버, PostgreSQL 동시 변경, Windows 운영 환경은 배포 후 확인해야 합니다. UI 브라우저 시각 검증은 별도입니다.
