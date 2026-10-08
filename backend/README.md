# Bumjin IT 자산관리 · Excel 파일 기반 파일럿

별도 DB 서버 없이 기존 테스트 엑셀을 읽고 수정하는 사내 실행용 웹앱입니다.

## 현재 구현

- BJM/BJE/BJC PC 목록, 검색·필터·페이지, 상세 조회
- 실제 자산 행마다 UUID 발급: 중복 관리번호도 QR 대상 분리
- 자산 등록, 사양·사용자·사용 상태 수정 및 XLSX 저장
- 원본 사업부별 열 위치와 구매일/구입년월/제조일 구분
- QR SVG 생성, 모바일 상세 주소, 라벨 인쇄
- 숨김 AZ열(asset ID), BA열(LDAP uid), 앱_이력·앱_LDAP사용자 시트 추가
- 저장 전 백업, 저장 버전 충돌 확인, 임시 파일 검증 후 교체
- 읽기 전용 LDAPS 사용자 가져오기 스크립트(현장 설정 필요)

## 실행: Windows PowerShell / Python 3.11 이상

압축을 풀고 해당 폴더에서 실행합니다.

간편 실행: PowerShell에서 `powershell -ExecutionPolicy Bypass -File .\start.ps1`을 실행하면 최초 환경 설치와 비밀번호 입력 후 앱이 시작됩니다. 현재 PowerShell 프로세스에만 실행 정책 옵션이 적용됩니다.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:APP_PASSWORD = Read-Host '테스트 관리자 비밀번호 (12자 이상)'
.\.venv\Scripts\python.exe app.py
```

브라우저: http://127.0.0.1:8080 / 사용자 이름 admin / 위에서 입력한 비밀번호.
중지: Ctrl+C. DB 설치는 필요 없습니다. 이 실행 환경에서는 의존성 다운로드가 제한돼 기존 설치 라이브러리로 핵심 기능을 시험했습니다. 패키지 설치와 LDAP는 사용자 환경에서 확인해야 합니다.

## 사용하는 엑셀과 Google Drive

data/Bumjin_IT자산관리대장.xlsx는 이 대화에서 Google Drive의 테스트 파일을 내려받은 스냅샷입니다. 앱은 이 파일에 직접 저장합니다. Drive 온라인 파일에 자동 업로드하는 기능은 아직 구현하지 않았습니다. 새로 테스트용 원본을 내려받으려면 **최초 실행 전에** data 파일을 교체하세요.

최초 실행은 백업 후 고유 ID와 앱 관리 시트를 추가합니다. 이후에는 이 앱용 엑셀을 계속 사용해야 QR 주소가 유지됩니다. 앱에서 엑셀 다운로드 후 같은 Drive 테스트 파일의 새 버전으로 올리는 것은 수동 절차입니다. Drive 실시간 연동을 원하면 후속 개발에서 인증·파일 버전 보호·자동 업로드 작업자를 구현해야 합니다.

## 휴대폰 QR 시험

PC와 휴대폰이 통신 가능한 사내망에서 실행합니다. 기본 localhost QR은 휴대폰에서 열리지 않습니다. 재실행 전에 PUBLIC_BASE_URL을 휴대폰에서 접속할 서버 주소로 설정해야 합니다.

```powershell
$env:HOST = '0.0.0.0'
$env:PUBLIC_BASE_URL = 'https://자산앱의-실제-사내주소'
.\.venv\Scripts\python.exe app.py
```

위 주소는 예시 자리표시자입니다. 사내 HTTPS 역방향 프록시를 구성하고 해당 주소로 휴대폰 접속을 확인한 뒤 QR을 출력하세요. 기본 서버는 HTTP이며 Basic 로그인은 TLS를 제공하지 않습니다. 실제 계정 비밀번호를 HTTP로 보내지 마세요. 이 파일럿은 관리자 공용 테스트 계정만 지원하며 LDAP 로그인·일반 직원 권한은 미구현입니다.

## Excel 운영 규칙

- 앱이 실행 중일 때 Excel이나 다른 프로그램으로 같은 파일을 편집하지 않습니다. 파일은 로컬 디스크에 둡니다. Drive 데스크톱 동기화 폴더/SMB 공유 파일의 다중 작성자를 지원하지 않습니다.
- 서버는 단일 프로세스로 실행합니다. lock 파일로 중복 시작을 막습니다. 네트워크 파일 잠금이나 외부 Excel 편집까지 강제로 통제하는 구조는 아닙니다.
- 충돌 검사에는 외부 프로그램이 검사와 교체 사이에 쓰는 경쟁 조건이 남으므로 단일 작성자 운영이 필수입니다.
- 데이터는 엑셀의 AZ/BA 숨김 열과 앱 시트에도 저장됩니다. 해당 열/시트를 삭제하지 않습니다.
- 모든 변경에 backups/ 사본을 만듭니다. 복구는 앱 종료 후 전체 XLSX 백업을 data 파일로 복사합니다.
- openpyxl은 Excel 계산 엔진이 아닙니다. 수식은 유지/추가하고 열 때 재계산하도록 설정합니다. 앱 현황은 데이터 행을 직접 집계합니다.
- 원본의 일반 셀·병합·수식을 점검했지만 Excel 전용 개체/인쇄 결과 전체의 시각 QA는 아직 수행하지 않았습니다. 파일럿에서 원본과 대조 후 운영 전환하세요.

## LDAP 사용자 목록 가져오기

LDAP 연동은 사내 네트워크에서만 가능합니다. 웹앱을 종료하고 실행합니다. 비밀번호를 코드나 문서에 기록하지 않습니다.

```powershell
$env:LDAP_HOST = 'ldap.bumjin.local'
$env:LDAP_BIND_DN = '실제-읽기용-계정-DN'
$env:LDAP_BIND_PASSWORD = Read-Host '읽기용 계정 비밀번호'
$env:LDAP_NAME_ATTRIBUTE = '화면의-설명-실명에-대응하는-확인된-속성'
$env:LDAP_ID_ATTRIBUTE = '조회가능한-안정적-식별-속성'
$env:LDAP_USER_FILTER = '검증된-직원계정-LDAP-검색필터'
$env:LDAP_CA_FILE = 'C:\인증서\사내CA.pem'
.\.venv\Scripts\python.exe sync_ldap.py
```

Base DN은 dc=ldap,dc=bumjin,dc=local, 사용자 검색 Base 후보는 cn=users,dc=ldap,dc=bumjin,dc=local입니다. 화면의 설명 열에 실명이 있으나 실제 LDAP 속성명은 직접 조회해 확정해야 합니다. LDAPS 636과 인증서 검증이 선행돼야 합니다.
가져오기 후 앱을 다시 열면 사용자 선택 목록에 표시됩니다. 사용자를 선택하면 Excel D열에는 실명, BA열에는 uid를 저장합니다. 계정 삭제를 자산 반납으로 처리하지 않습니다. 기존 이름의 자동 대량 매핑/정기 동기화/계정 상태 재검증은 다음 단계입니다. 동명이인은 uid와 이메일을 대조해 선택하세요.

## 아직 구현하지 않은 범위

Google Drive 자동 동기화, LDAP 로그인·자동 이름 매핑, 직원별 권한, 인수·반납 승인, 모니터/태블릿 편집, 사업부 이동, 백그라운드 동기화는 후속 개발 항목입니다. 현재는 관리자가 PC를 직접 등록·배정하는 파일럿입니다. 구매일이 없는 BJC는 제조일만 편집합니다. 별도 구매일 저장은 확장 필요합니다.

## 검증

```powershell
.\.venv\Scripts\python.exe test_app.py
```

테스트는 임시 복사본만 변경하며 실제 data 파일을 건드리지 않습니다. 새 자산 추가, 수정, ID 유지, 충돌 차단, 엑셀 수식 주입 차단, QR SVG, 로그인 보호, 원본 시트 보존을 검사합니다.
