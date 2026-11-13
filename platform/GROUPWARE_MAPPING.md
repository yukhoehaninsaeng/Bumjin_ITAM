# 그룹웨어 부서와 LDAP 계정 매핑

부서명·조직 계층·재직 상태는 그룹웨어 Excel이 원본이며, LDAP은 uid·고유 ID와 비밀번호 인증만 담당합니다. LDAP의 부서 속성은 사용하거나 수정하지 않습니다.

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
