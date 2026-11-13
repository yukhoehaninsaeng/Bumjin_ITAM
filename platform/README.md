# BUMJIN IT Python 통합 플랫폼 — 1차 구현

이 디렉터리는 [PRD](../docs/PRD.md), [UI 명세서](../docs/UI_SPEC.md), [TRD](../docs/TRD.md)를 기반으로 한 **별도 실행 앱**입니다. 기존 `backend/`, `public/`, `docker-compose.yml`과 운영 XLSX를 자동으로 수정하거나 새 앱으로 전환하지 않습니다.

## 현재 동작하는 범위

- Django 5.2 LTS·PostgreSQL 기반 DB migration, 개인별 DB 세션 로그인.
- `/admin` 통합 대시보드, 역할·법인·지원팀으로 제한된 티켓 큐.
- `/portal` 직원 홈, 본인 요청·현재 배정 자산 조회.
- 관리자 대리 등록·직원 요청 등록, 요청자와 등록자 구분, 자산 연결.
- 사진·파일 선택, 드롭, Ctrl+V / ⌘+V 이미지 붙여넣기.
- 서버 임시 업로드, 이미지 검증, 최대 5개·파일당 20MB, 티켓 귀속, 권한 검사 다운로드.
- 담당자 인수, 우선순위·목표 처리일 변경, 공개 답변·내부 메모, 상태 변경·해결·직원 확인·재개.
- 생성 요청 멱등키와 행 버전 검사, DB 트랜잭션·감사 이력.
- 자산 등록·법인별 자동 채번·직원 배정·반납, 기존 자산 UUID용 공개 QR 단건 조회 경로.
- 조직·직원 범위 조회, 현재 조직별 직원 카드.
- 명시적으로 연결한 LDAP 고유 ID의 LDAPS 로그인 backend (실제 사내 LDAP 검증은 별도 필요).
- 운영자 생성, 개발 전용 샘플 생성, 읽기 전용 기존 원장 검사, 만료 임시 첨부 정리 명령.

## 아직 연결하지 않은 범위

전체 PRD가 완료된 운영 배포판은 아닙니다. 다음은 후속 개발·검증 대상입니다.

- 기존 원장의 실제 DB 이관 적용·대조, 기존 SW·계약·과거 이력의 정규 모델 이관.
- 조직 편집·직원 이동·퇴사의 운영 UI와 개인별 역할·계정 설정 UI.
- LDAP 자동 가져오기·부서 쓰기·UNKNOWN 결과 재확인 worker.
- 티켓 팀 이동·타 담당자 재배정 UI, 대화 수정 이력, 답변 첨부, 앱 내 알림·백그라운드 작업.
- 기존 PDF·QR 이미지·라벨 출력, 계약 편집·재물조사·대량 Excel 입출력의 새 앱 연결.
- NAS 실장비 부하·전체 복구 시험과 운영 전환.

`/admin/settings`는 연결 예정 범위를 설명하는 화면입니다. 현재 비밀값·연결 설정은 환경변수, 역할은 관리 명령·DB에서만 관리합니다. 권한 확인 없이 실제 운영 계정을 수동 승격하거나 LDAP 비밀번호를 로컬 계정에 복제하지 않습니다.

## 로컬 개발: PostgreSQL 권장

Python 3.12와 PostgreSQL 16을 준비합니다. 기존 운영 DB·원장 사본과 분리된 개발 DB를 사용하세요.

```bash
cd platform
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export ITAM_DEBUG=1
export DATABASE_URL='postgresql://itam:YOUR_URL_ENCODED_PASSWORD@127.0.0.1:5432/itam'
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py bootstrap_admin --username operator --company BJC --name '운영 담당자'
python manage.py runserver 127.0.0.1:8000
```

비밀번호는 프롬프트에서 입력합니다. 기본 관리자 비밀번호는 없습니다. 관리자는 `/admin`, 직원은 `/portal`에 접근합니다. 운영자는 생성한 법인에만 네 가지 관리 역할을 부여받습니다. Django 기본 admin 화면은 `/admin`에 연결하지 않습니다.

샘플 화면을 확인하려면 **빈 개발 DB에서** `python manage.py seed_demo`를 실행합니다. DEBUG 환경에서만 실행되며 비밀번호를 직접 입력해야 합니다. `demo-admin`, `demo-employee` 두 계정이 생성됩니다. 서버 시작 시 자동으로 샘플 데이터를 생성하지 않습니다.

SQLite는 명시적으로 `ITAM_DEBUG=1 ITAM_DEV_SQLITE=1`을 설정한 경우에만 허용합니다. 화면 개발용이며 PostgreSQL 동시성 검증이나 운영에 사용하지 않습니다.

## 별도 Docker 실행

저장소 루트에서 기존 Compose와 구분해 실행합니다. 실제 비밀값은 Git 밖의 권한 제한 파일로 준비합니다. `platform/.env.example`은 이름·형식의 예시이며 자동 로딩되지 않습니다.

```bash
# /secure/itam-platform.env: DJANGO_SECRET_KEY, PLATFORM_DB_PASSWORD 등 설정
# DB 비밀번호는 URL-safe 값으로 생성합니다.
docker compose --env-file /secure/itam-platform.env -f compose.platform.yml build
docker compose --env-file /secure/itam-platform.env -f compose.platform.yml up -d db
# migration은 web worker마다 자동 실행하지 않고 한 번만 수행합니다.
docker compose --env-file /secure/itam-platform.env -f compose.platform.yml run --rm web python manage.py migrate
docker compose --env-file /secure/itam-platform.env -f compose.platform.yml run --rm web python manage.py bootstrap_admin --username operator --company BJC --name '운영 담당자'
docker compose --env-file /secure/itam-platform.env -f compose.platform.yml up -d web
```

- 새 DB는 `platform_db`, 첨부는 `platform_files` named volume에 저장됩니다.
- 기존 Excel `data` 디렉터리를 mount하지 않습니다.
- web은 `127.0.0.1:1881`에만 노출합니다. 기존 운영 1880 포트를 교체하지 않습니다.
- 운영은 DEBUG=0이며 HTTPS로 서비스해야 합니다. Synology 역방향 프록시를 구성하고 실제 도메인을 ALLOWED_HOSTS·CSRF origins에 등록합니다. 컨테이너 healthcheck용 `127.0.0.1`도 ALLOWED_HOSTS에 포함합니다.
- 프록시가 외부의 forwarding 헤더를 제거하고 검증된 값을 설정하는 경우에만 `ITAM_TRUST_HTTPS_PROXY=1`을 사용합니다.
- web은 non-root·읽기 전용 rootfs로 실행하며 `/data`·`/tmp`만 씁니다. DB 포트는 호스트에 공개하지 않습니다.
- 호스트 자원에 따라 Gunicorn worker·thread 수와 DB 연결 수를 검증해야 합니다.
- 사내 LDAPS를 사용하려면 CA 파일 read-only mount와 `.env.example`의 LDAP 환경변수를 web 서비스에 추가해야 합니다. 현재 Compose에 LDAP 비밀값을 기본 주입하지 않습니다.

DB·파일·설정을 함께 백업해야 합니다. 이 Compose를 실행한 것만으로 PRD의 RPO/RTO·NAS 운영 전환 조건이 충족되지는 않습니다.

## 운영 명령

```bash
# 원장 사본 검증: DB·XLSX에 쓰지 않음, 자동 적용 기능 없음
python manage.py preview_legacy /path/to/CURRENT_WORKBOOK_COPY.xlsx
# 만료 임시 첨부 미리보기
python manage.py cleanup_uploads
# 실제 정리: 티켓에 귀속된 첨부는 유지
python manage.py cleanup_uploads --apply
```

만료 파일 정리는 하루 한 번 별도 스케줄러에서 실행할 수 있습니다. 연결된 ticket·draft 소유권 기록은 보존합니다. 프로세스 강제 종료 등으로 생긴 DB 참조 없는 파일의 보관·정리 자동화는 후속 보완 대상입니다.

`preview_legacy`는 원본 파일 checksum, 법인·장비별 수량, 누락 시트, UUID·관리번호·상태 오류, 직원 매핑 검토 대상만 보고합니다. 이름이 같다는 이유로 계정을 자동 연결하지 않습니다. 운영 원장 대신 사본을 검사하고 전체 적용 명령은 후속 이관 검증 후 추가합니다.

## 주요 보안·데이터 규칙

- 직원 API는 서버가 요청자·법인을 확정하고 내부 필드 입력을 거부합니다.
- system_admin 역할만으로 티켓·자산 전체 접근을 허용하지 않습니다.
- 다른 역할의 법인 범위가 support_agent 역할에 합쳐지지 않습니다.
- 티켓 생성은 UUID 형식 `Idempotency-Key`를 요구합니다. 재시도 때 같은 키·같은 본문을 사용합니다.
- 상태·담당·처리 설정·자산 배정에는 `expected_version`이 필요합니다.
- 첨부 원본명은 저장 경로로 쓰지 않습니다. private file 경로를 정적 서버로 공개하면 안 됩니다.
- PDF·Office·이미지·UTF-8 텍스트의 내용을 검사합니다. 현재 악성코드 검사 엔진은 포함하지 않습니다.
- 로그인·LDAP 비밀번호·세션 값은 로그나 응답에 포함하지 않습니다.
- 기존 LDAP viewer 계정을 관리자 권한으로 자동 전환하지 않습니다.

## 테스트

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py collectstatic --noinput
python manage.py test operations --noinput
node --check operations/static/operations/workspace.js
node --check operations/static/operations/uploads.js
```

PostgreSQL 테스트는 임시 `test_` DB 생성·삭제 권한이 있는 **개발 DB 계정**이 필요합니다. CI는 격리된 PostgreSQL 16을 사용합니다. 동시 배정·중복 생성 테스트는 PostgreSQL 행 잠금을 실제로 검증하며 SQLite에서는 생략됩니다.

개발 중 Chromium에서 실제 로그인, 직원 이미지 paste 업로드·재조회·다운로드, 담당 인수·내부 메모·해결, 직원 해결 확인, 모바일 화면을 확인했습니다. 실제 NAS·LDAP 인증서·원장 적용은 검증하지 않았습니다.
