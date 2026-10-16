# Synology NAS 설치·운영 안내

최신 설치·운영·개발 인수인계 문서는 [README.md](README.md)입니다. 실행 명령과 데이터 구조가 여러 문서에서 달라지지 않도록 README를 기준으로 관리합니다.

## 실행 방식 선택

- **DSM의 Docker 패키지 / SSH CLI:** README 7절의 `docker build`와 `docker run` 절차를 사용합니다. Container Manager GUI가 필수는 아닙니다.
- **Container Manager / Compose:** README 7.8절을 사용합니다. 저장소의 `docker-compose.yml` 서비스 키는 `itam`입니다.
- 두 방식을 같은 포트와 원장에 동시에 실행하지 않습니다.

## 작업별 바로가기

| 작업 | 문서 |
| --- | --- |
| 최초 설치, CMD에서 SSH 접속, 파일 배치 | [README 7절](README.md#7-synology-nas-설치와-실행) |
| 이미지 업데이트와 이전 컨테이너 복귀 | [README 8절](README.md#8-업데이트와-이전-버전-복귀) |
| 엑셀만으로 업무 데이터 복구 | [README 9절](README.md#9-백업과-데이터-복구) |
| LDAP 연결·인증서·조직·부서 쓰기 | [README 11절](README.md#11-ldap-연동과-조직도-관리) |
| 컨테이너 실패·잠금·권한·DNS 장애 | [README 14절](README.md#14-장애-대응) |
| 후임자 최종 확인표 | [README 17절](README.md#17-최종-인수인계-확인표) |

## 반드시 보존할 것

- 프로젝트 루트의 `data/Bumjin_IT자산관리대장.xlsx`: 실제 업무 원장. 컨테이너에서는 `/data/Bumjin_IT자산관리대장.xlsx`.
- `data/admin_settings.json`: 관리자 비밀번호 해시와 LDAP 설정.
- `data/ldap_department_audit.jsonl`: LDAP 쓰기 진단 기록.
- 직접 실행의 `.env.runtime` 또는 Compose의 `.env`, 필요한 CA 인증서.
- 기존 QR의 자산 UUID와 접속 주소.

`data`, 환경설정, 인증서를 새 소스로 덮어쓰지 않습니다. 컨테이너 재시작만으로 코드가 업데이트되지는 않습니다. 실제 NAS 모델·DSM·폴더 권한·DNS·LDAP·프린터는 설치 환경에서 확인하세요.
