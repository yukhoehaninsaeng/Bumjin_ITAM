# 티켓 알림 메일 / 메일 답장 등록

## 기능

| 이벤트 | 알림 수신자 |
| --- | --- |
| 접수 | 요청자와 접근 가능한 지원팀 |
| 담당하기 | 요청자와 배정된 담당자 |
| 공개 답변 | 작성자를 제외한 요청자/담당자, 미배정 시 지원팀 |
| 상태 변경 | 변경자를 제외한 요청자/담당자 |
| 내부 메모 | 발송 안 함 |

요청자에게는 **내 문의 바로가기**, 담당자에게는 **티켓 바로가기** 버튼을 제공합니다. 기존 로그인/권한 검사 후 해당 문의로 이동하며 로그인 우회 링크는 아닙니다. 메일에 제목·담당자·현재 상태만 표시하므로 내부 메모와 대화 본문이 메일로 노출되지 않습니다.

메일에 답장하면 새 텍스트를 공개 답글로 등록합니다. 알림별 무작위 Message-ID, 정확한 발신 주소, 현재 티켓 권한, 신뢰하는 수신 게이트웨이의 DMARC pass가 필요합니다. 제목만으로 문의를 추측하지 않습니다. 메일별 중복 방지, 부재중/반송/리스트 차단, 사용자별 시간당 50건 제한이 있습니다. 원본 알림 90일 경과 또는 종료/취소 티켓은 메일 답장을 거절합니다.

## 적용

기존 Supabase 정보가 있는 config/settings.py를 유지한 채 코드를 업데이트합니다.

```powershell
cd 'C:\Users\BJE\Desktop\AI_PJT\Bumjin_ITAM-main\platform'
.\.venv\Scripts\python.exe manage.py migrate
```

메일 테이블 두 개를 추가합니다. PostgreSQL에서는 RLS를 활성화하며 서버 DB 계정이 테이블 소유자 또는 BYPASSRLS 역할이어야 합니다. Supabase anon/authenticated용 공개 접근 정책은 추가하지 마세요.

## 회사 메일 설정

회사 그룹웨어/NAS에 티켓 전용 메일함을 준비합니다. SMTP와 IMAPS를 사용할 수 있어야 합니다. 사용자별 Employee.email을 입력하세요. 비어 있으면 연결된 Django User.email을 사용합니다. 계정 ID로 이메일을 추측하지 않습니다.

`platform/ticket_mail_settings.example.py`의 ITAM_MAIL 사전을 로컬 config/settings.py 맨 아래로 복사합니다. 실제 SMTP/IMAP 호스트·포트·계정·비밀번호를 입력합니다. 이 파일에 비밀번호를 넣었다면 GitHub로 올리지 마세요. 각 설정은 ITAM_MAIL_ 접두어 환경변수로도 전달할 수 있으며 사전 값이 우선합니다.

- PUBLIC_BASE_URL: 직원들이 접근 가능한 **ITAM 프로그램 웹 주소**. Supabase DB 주소가 아닙니다. 127.0.0.1은 수신자 자신의 기기를 가리키므로 공유 메일 링크에 쓰지 않습니다. 운영은 HTTPS 필수입니다. 사내 주소라면 모바일도 회사망/VPN 접속이 필요합니다.
- SMTP 587 STARTTLS: SMTP_USE_TLS=1, SMTP_USE_SSL=0. SMTP 465 SSL이면 반대로 설정합니다. 인증서 검증을 유지합니다.
- IMAP 993 SSL: 전용 수신함(INBOX)을 사용합니다. 사설 CA이면 IMAP_CA_FILE에 PEM 경로를 지정합니다.
- AUTH_SERV_ID: 수신 게이트웨이가 추가하는 Authentication-Results 첫 토큰. 메일 관리자에게 확인해야 합니다. 게이트웨이는 외부에서 위조된 동일 authserv-id 헤더를 제거하고 자체 검증 헤더를 추가해야 합니다. 내부 메일에도 DMARC pass가 제공되어야 합니다. 이 조건이 지원되지 않으면 INBOUND_ENABLED=0으로 두고 제공자별 인증 수신 API가 필요합니다.

처음에는 ENABLED=1, INBOUND_ENABLED=0으로 발송만 테스트합니다. 게이트웨이 인증 확인 후 INBOUND_ENABLED=1을 켭니다. 기본 설정은 둘 다 꺼져 있습니다. SMTP 비밀번호나 인증 헤더를 채팅으로 보내지 마세요.

## 실행

웹 서버와 별도 PowerShell 창에서:

```powershell
# 한 번 처리
.\.venv\Scripts\python.exe manage.py process_ticket_mail

# 60초마다 처리. 테스트 중 창을 열어 둡니다.
.\.venv\Scripts\python.exe manage.py process_ticket_mail --watch --interval 60

# 발송 상태 / 수신 거절 사유 집계
.\.venv\Scripts\python.exe manage.py process_ticket_mail --status

# 장애 해결 후 5회 실패 메일을 명시적으로 다시 시도
.\.venv\Scripts\python.exe manage.py process_ticket_mail --retry-failed
```

서버 이전 후 systemd/Docker 별도 프로세스로 --watch를 실행하거나 NAS 작업 스케줄러에 단발 명령을 등록합니다. IMAP 작업자는 하나만 운영하세요. 이 PR이 스케줄러를 자동 설치하지는 않습니다.

SMTP는 티켓 트랜잭션과 함께 저장되는 DB 대기열에서 발송합니다. 실패해도 티켓 저장은 유지되며 최대 5번 지연 재시도합니다. SMTP 접수 직후 프로세스 종료 시 같은 Message-ID의 중복 발송 가능성은 있습니다.

IMAP은 안 읽은 메일만 조회하고 처리 후 읽음으로 표시합니다. 원본을 삭제하지 않습니다. 다른 클라이언트가 먼저 읽거나 자동으로 이동하지 않도록 전용 메일함을 사용하세요. DB 오류 등 처리 중단 시 읽음 처리하지 않습니다.

## 검증과 제한

요청자/담당자 각각 테스트 계정으로 접수 → 담당하기 → 공개 답변 → 상태 변경 알림과 링크를 확인하세요. 내부 메모는 요청자에게 발송되면 안 됩니다. 메일 답장 후 공개 댓글 1개와 작성자, 중복 방지를 확인하세요. 실제 회사 SMTP/IMAP·모바일 링크·PostgreSQL 동시 처리는 배포 환경에서 확인해야 합니다.

메일 첨부는 자동 등록하지 않으며 본문에 안내를 남깁니다. 첨부는 문의 화면에서 등록하세요. 원문 2MB 또는 새 본문 10,000자 초과는 거절합니다. HTML은 텍스트로 처리하고 대표적인 인용 구분선 위 내용만 추출하므로 모든 클라이언트의 서명/인용 형식을 완벽히 분리하지는 못합니다. 거절 메일에 자동 회신하지 않습니다. --status와 원본 메일함에서 확인하세요. 메일 답장만으로 종료 문의를 재개하거나 내부 메모를 작성하지 않습니다.

설정이 꺼진 동안의 과거 이벤트는 소급 발송하지 않습니다. 실패·거절 결과에는 본문이나 비밀번호를 저장하지 않습니다.

LDAP mail 주소를 가져오려면 다음 명령으로 미리보기 후 적용합니다. 빈 주소는 기존 값을 지우지 않습니다.

```powershell
.\.venv\Scripts\python.exe manage.py sync_ldap_accounts --sync-email
.\.venv\Scripts\python.exe manage.py sync_ldap_accounts --sync-email --apply
```

미리보기의 email_updates/email_missing을 확인하세요. LDAP 부서·계정·비밀번호는 수정하지 않습니다.
