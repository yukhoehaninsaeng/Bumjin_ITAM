# 그룹웨어 부서와 LDAP 계정 매핑

부서 트리와 최초 소속·재직 상태는 그룹웨어 Excel로 구성합니다. 프로그램에서 수동 지정한 소속을 우선 유지하며, 관리자가 저장할 때 NAS LDAP의 확인된 부서 속성에 부서명을 반영합니다. LDAP의 uid·고유 ID로 계정을 연결하며 비밀번호 인증은 LDAP에서 처리합니다.

## 최초 적용

서버를 중지하고 DB를 백업한 다음 이 브랜치의 코드를 적용합니다. 로컬 Supabase 비밀번호를 넣은 `config/settings.py`는 유지합니다.

```powershell
cd 'C:\Users\BJE\Desktop\AI_PJT\Bumjin_ITAM-main\platform'
.\.venv\Scripts\python.exe manage.py migrate
```

받은 두 Excel 파일을 `platform\data` 폴더에 복사하고 먼저 미리보기를 실행합니다.

```powershell
.\.venv\Scripts\python.exe manage.py sync_groupware --departments '.\data\부서목록_20261113.xlsx' --members '.\data\멤버목록_20261113.xlsx'
```

이번 원본은 부서 164개(부서코드 없는 표시 조직 10개 포함), 멤버 509명, 재직 328명, 퇴사 181명입니다. `conflicts`가 비어 있고 법인별 조직 수가 맞으면 같은 명령에 `--apply`를 붙입니다.

```powershell
.\.venv\Scripts\python.exe manage.py sync_groupware --departments '.\data\부서목록_20261113.xlsx' --members '.\data\멤버목록_20261113.xlsx' --apply
```

- 부서가 빈 행은 퇴사 처리하고 연결된 로그인은 비활성화합니다. 기존 직원과 자산 배정·티켓 이력은 삭제하지 않습니다.
- 이번 파일에 없는 기존 계정은 그대로 둡니다. 따라서 DB 전체 인원수는 원본 인원수와 다를 수 있습니다.
- 사용자 매칭은 `아이디` = 기존 `groupware_uid` / LDAP `directory_uid` / 연결 로그인 ID의 정확한 일치만 사용합니다. 이름·이메일로 추측하지 않습니다. 둘 이상이면 적용 전체를 중단합니다.
- 그룹웨어 아이디가 새로 들어오면 직원 레코드를 만들지만 로그인 계정은 아직 만들지 않습니다. LDAP 고유 ID를 확인한 다음 연결합니다.
- 새로 나타난 퇴사자는 법인을 알 수 없으므로 BJE 관리 범위의 별도 보관 조직에 넣고 `company_unconfirmed`로 기록하며 화면에는 법인을 미확인으로 표시합니다.
- 정해진 부서코드는 유지하고 명칭/상위 조직을 수정할 수 있습니다. 같은 코드의 조직 UUID는 유지됩니다. 코드 없는 표시 조직은 상위 코드와 명칭으로 만든 내부 키를 쓰며 직원 배정에 사용하지 않습니다.
- 기존 부서와 이름이 같고 같은 계층 위치에 있는 경우에만 유일한 기존 조직을 연결합니다. 이름이 바뀐 기존 자산 관리 조직은 추측해서 일괄 변경하지 않습니다.

## 법인 기준

사용자 결정에 따라 본사·경영기획·인사정보·재무회계·국내 전자사업 조직은 BJE입니다. 상위 코드 BJC는 BJC, BJM52와 IM_SUP는 BJM, BJIS42는 BJIS, BJV/HBC/IPOC/BEI/BJAM/HBJ는 각각 해당 법인으로 분류합니다. 법인 정보는 그룹웨어에 별도 컬럼이 없으므로 운영 정책입니다. 그룹웨어 코드 접두어만으로 소속을 추측하지 않습니다.

다른 기준이 필요하면 JSON 객체 `{"부서루트코드":"법인코드"}`를 작성하고 `--company-map 파일.json`으로 미리보기/적용합니다. 매핑은 최상위 코드에서 하위 조직으로 전파됩니다. 기존 역할·법인 조회 권한은 자동 확장하지 않습니다.

## LDAP 연결

기존 `LDAP_HOST`, `LDAP_PORT`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD`, `LDAP_BASE_DN`, `LDAP_CA_FILE`, `LDAP_ID_ATTRIBUTE` 설정을 사용합니다. `LDAP_DIRECTORY_SOURCE` 기본값은 `synology`입니다. 실제 사내 LDAPS 연결과 CA 검증이 필요합니다.

```powershell
.\.venv\Scripts\python.exe manage.py sync_ldap_accounts
.\.venv\Scripts\python.exe manage.py sync_ldap_accounts --apply
```

- 그룹웨어에 있는 재직 사용자만 uid의 정확한 일치로 연결합니다.
- LDAP 고유 ID가 바뀌거나 다른 직원에게 이미 연결되어 있으면 중단합니다.
- 새 로그인에는 로컬 비밀번호를 만들지 않습니다. 실제 로그인은 DirectoryBackend가 LDAPS로 인증합니다.
- 동일한 로그인 ID가 이미 별도 계정으로 존재하면 자동 인수하지 않고 수동 연결을 요구합니다.
- 퇴사자와 그룹웨어에 없는 LDAP 계정은 활성 로그인으로 만들지 않습니다.
- 그룹웨어에서 비활성화했던 로그인만 복직 시 복구합니다. 관리자가 별도로 비활성화한 계정을 임의로 활성화하지 않습니다.

## 이후 최신화

새 부서 목록·멤버 목록으로 `sync_groupware`를 미리보기 후 적용하고, 새 LDAP 계정이 생겼으면 `sync_ldap_accounts`를 미리보기 후 적용합니다. 부서코드나 계정 ID 자체가 변경되면 자동 이름 매칭을 하지 않으므로 운영자가 구/신 코드 매핑을 검토해야 합니다.

개발 검증은 임시 SQLite에서 수행했습니다. 운영 PostgreSQL과 실제 LDAP 연결은 별도로 확인해야 합니다.

## 프로그램에서 지정한 부서를 NAS LDAP에 반영

사용자·조직 화면 → 사용자 행의 **부서 지정 / 재반영** → 부서 선택 → **저장 및 LDAP 반영**.
프로그램에 소속을 먼저 저장하고 Synology LDAP Server 사용자 정보 → 자세히 → 부서에 해당하는 속성에 부서명을 기록합니다.
부서코드와 상하위 조직은 프로그램에서 유지합니다. 사용자 DN, 그룹, 공유폴더 권한, 비밀번호는 변경하지 않습니다.

- 조직 관리 권한이 있는 법인의 직원과 대상 부서만 수정할 수 있습니다.
- LDAP 계정 연결(`sync_ldap_accounts --apply`)을 마친 재직자만 NAS에 반영합니다.
- `반영 완료`는 LDAP에 쓴 값을 다시 조회하여 일치한 상태입니다. NAS UI의 필드 연결은 아래 최초 확인 절차로 검증해야 합니다.
- 연결 실패/설정 미완료도 프로그램의 부서 지정은 보존됩니다. 실패 사유를 확인하고 같은 버튼으로 다시 시도합니다. 자동 백그라운드 재시도는 없습니다.
- 프로그램에서 수동 지정한 부서는 이후 그룹웨어 Excel 업로드가 덮어쓰지 않습니다. Excel의 빈 부서코드는 여전히 퇴사 처리됩니다.
- 이 기능은 사용자가 선택한 직원 한 명씩 반영합니다. Excel 재업로드, 부서명 변경, 퇴사 처리만으로 NAS에 일괄 쓰거나 계정을 비활성화하지 않습니다. 부서명이 변경된 경우 상태가 `반영 대기`로 표시되며 다시 반영해야 합니다.

### 1. 접속 설정

로컬 `platform/config/settings.py` 맨 아래에 다음 설정을 추가합니다. 기존 DATABASES는 유지합니다.
실제 비밀번호를 넣은 settings.py는 GitHub에 올리지 마세요. 운영에서는 같은 이름의 환경변수로 전달할 수 있습니다.

```python
ITAM_LDAP = {
    "LDAP_HOST": "인증서에_등록된_NAS_호스트명",
    "LDAP_PORT": "636",
    "LDAP_BASE_DN": "dc=example,dc=com",  # NAS에 표시된 실제 Base DN
    "LDAP_BIND_DN": "부서_속성_수정권한이_있는_계정의_실제_DN",
    "LDAP_BIND_PASSWORD": "여기에_해당_계정_비밀번호",
    "LDAP_CA_FILE": "",  # 사설 인증서이면 신뢰할 CA PEM 파일의 절대 경로
    "LDAP_DIRECTORY_SOURCE": "synology",
    "LDAP_ID_ATTRIBUTE": "entryUUID",
    "LDAP_DEPARTMENT_ATTRIBUTE": "",  # 아래 조회 결과 확인 후 입력
    "LDAP_DEPARTMENT_WRITE_ENABLED": "0",  # 확인 전에는 쓰지 않음
}
```

쓰기 대상은 Synology LDAP **Provider** 서버여야 합니다. Consumer는 읽기 전용입니다.
LDAPS 인증서 검증을 끄지 않습니다. 사설 CA는 LDAP_CA_FILE로 지정합니다.

### 2. NAS '부서' 칸의 실제 속성 확인 (최초 한 번)

공식 문서가 부서 UI의 내부 LDAP 속성명을 명시하지 않아 `ou`나 `departmentNumber`를 임의로 선택하지 않습니다.

1. NAS에서 **테스트용 LDAP 계정** 하나의 기존 부서 값을 기록합니다.
2. 사용자 정보 → 자세히 → 부서에 고유 문자열 `ITAM-DEPT-CHECK-20261113`을 입력하고 저장합니다.
3. PowerShell에서 platform 폴더로 이동한 뒤 조회합니다(테스트 계정 ID를 교체).

```powershell
.\.venv\Scripts\python.exe manage.py probe_ldap_department --uid '테스트계정ID' --value 'ITAM-DEPT-CHECK-20261113'
```

명령은 읽기 전용이며 정확히 일치한 **속성명만** 출력합니다. 다른 LDAP 개인정보·비밀번호는 출력하지 않습니다.
출력된 `LDAP_DEPARTMENT_ATTRIBUTE=...`의 값을 settings.py의 같은 항목에 넣습니다.
지원 속성은 departmentNumber, department, ou, synoDepartment입니다. 다른 속성이 나오면 결과 이름만 확인하고 지원을 추가하기 전 쓰기를 켜지 않습니다.

4. NAS 테스트 계정의 부서 값을 원래 값으로 복원합니다.
5. `LDAP_DEPARTMENT_WRITE_ENABLED`를 `"1"`로 바꾸고 서버를 재시작합니다.
6. 연결된 테스트 직원 한 명의 부서를 프로그램에서 변경하고, NAS 부서 칸에 같은 값이 보이는지 확인합니다.

### 3. 계정 연결 후 사용

```powershell
.\.venv\Scripts\python.exe manage.py sync_ldap_accounts
.\.venv\Scripts\python.exe manage.py sync_ldap_accounts --apply
```

위 명령은 계정 연결만 수행합니다. 부서 쓰기는 사용자·조직 화면의 저장 버튼에서 실행합니다.
실제 NAS와의 연결 및 UI 반영은 배포 환경에서 검증해야 합니다. 자동 테스트는 LDAP 응답을 모의하여 검증합니다.
