# Synology NAS 설치

## 준비

Container Manager 설치가 가능한 NAS와 DSM이 필요합니다. 패키지 센터에서 설치 가능 여부를 먼저 확인하세요. 이 구성은 별도 DB 없이 NAS 폴더의 Excel을 사용합니다.

## 1. 파일 배치

최신 저장소 ZIP을 내려받아 File Station으로 업로드하고 압축을 풉니다. 예시 프로젝트 폴더: `docker/bumjin-itam`.

프로젝트 루트에 다음이 있어야 합니다.

- Dockerfile, docker-compose.yml
- backend, public
- 직접 만든 data, certs 폴더
- .nas.env.example을 복사한 .env 파일

PC 앱을 종료하고 **사용하던 앱용 Excel**을 프로젝트 루트의 `data/Bumjin_IT자산관리대장.xlsx`에 복사하세요. 기존 QR용 UUID를 유지하려면 새 원본으로 교체하지 않습니다.

기존 관리자 설정을 유지하려면 PC의 `backend/data/admin_settings.json`도 NAS의 `data`로 복사합니다. 이 파일은 비밀번호 해시와 LDAP 설정을 포함합니다. 기존 CA 파일 경로는 컨테이너의 `/certs` 경로로 변경해야 합니다.

## 2. 환경 설정 및 폴더 권한

.nas.env.example을 .env로 복사하고 설정합니다.

```dotenv
ITAM_PUBLIC_BASE_URL=http://YOUR_NAS_IP:1880
ITAM_ADMIN_PASSWORD=12자이상의테스트비밀번호
ITAM_UID=실제NAS계정UID
ITAM_GID=실제NAS계정GID
```

위 값은 자리표시자입니다. 실제 NAS 주소와 계정의 숫자 UID/GID를 입력하세요. 필요한 경우 DSM에서 SSH를 활성화하고 해당 계정으로 `id`를 실행해 확인합니다. 설정을 마치면 불필요한 SSH 접근은 종료하세요.

해당 계정에 data 폴더의 읽기·쓰기, certs의 읽기 권한을 줍니다. 기존 admin_settings.json도 해당 UID가 읽고 쓸 수 있어야 합니다. 기본 예시의 1000이 NAS 계정 UID와 일치한다고 가정하지 마세요.

.env 파일의 접근 권한은 관리자에게만 부여하세요. 최초 비밀번호는 APP_PASSWORD로 사용하고, 웹 관리자 페이지에서 변경하면 data의 저장된 비밀번호 해시가 우선합니다.

## 3. 프로젝트 생성

1. Container Manager → 프로젝트 → 생성
2. 이름: bumjin-itam
3. 경로: 위 프로젝트 폴더
4. 해당 폴더의 docker-compose.yml 사용
5. 빌드 후 시작

Python 이미지 다운로드와 패키지 설치에 외부 인터넷 접근이 필요합니다. 최초 빌드가 끝나면 컨테이너 로그에서 시작 여부를 확인합니다.

## 4. 접속

- 메인: `http://YOUR_NAS_IP:1880`
- 관리자: `http://YOUR_NAS_IP:1880/admin`
- 사용자: `http://YOUR_NAS_IP:1880/users`
- 로그인: admin / 최초 설정 비밀번호 또는 이전에 변경한 비밀번호

1880이 다른 서비스에서 사용 중이라면 호스트 포트만 다른 값으로 바꾸고 PUBLIC_BASE_URL에도 같은 주소를 적용하세요. NAS 방화벽에서는 필요한 사내 대역만 허용합니다. 라우터의 인터넷 포트 개방은 필요하지 않습니다.

## 5. HTTPS 및 고정 QR 주소

실제 운영 전에 고정된 HTTPS 앱 주소를 권장합니다.

DSM → 제어판 → 로그인 포털 → 고급 → 역방향 프록시에서, 인증서와 연결되는 앱 호스트의 HTTPS 요청을 HTTP 127.0.0.1:1880으로 전달합니다. DNS 또는 사내 DNS에서 앱 호스트를 NAS로 연결하고 해당 호스트에 맞는 인증서를 지정합니다.

.env의 ITAM_PUBLIC_BASE_URL을 실제 HTTPS 주소로 바꾸고 프로젝트를 재생성·재시작합니다. 이후 생성한 QR은 새 주소를 사용합니다. PC IP를 담은 기존 인쇄 QR은 다시 출력해야 합니다. DSM 관리 화면의 주소와 자산관리 앱 주소는 다릅니다.

## 6. LDAP

/admin에서 LDAPS 설정을 입력합니다. LDAP 호스트는 컨테이너에서 접근 가능한 이름이어야 하고 인증서와 일치해야 합니다. 필요 시 Compose extra_hosts 또는 DNS 설정을 사용합니다.

CA 파일은 프로젝트 certs에 배치하고 앱에서는 `/certs/파일명.pem`으로 지정합니다. 사용자·조직 가져오기로 확인하세요. Bind 비밀번호는 메모리 보관이므로 컨테이너 재시작 후 다시 입력합니다.

## 데이터·업데이트

- NAS의 data가 원장이며 컨테이너를 지워도 이 폴더는 유지합니다.
- 동일 파일을 PC 서버와 NAS 서버에서 동시에 사용하지 마세요.
- 업데이트: 프로젝트 중지 → data·.env·certs 보존 → 코드 교체 → 이미지 재빌드 → 시작.
- data 전체를 NAS 백업 대상에 포함하세요.
- 앱 실행 중 동일 Excel을 직접 편집하지 않습니다.

정상 컨테이너 종료에서는 잠금 파일을 정리하도록 SIGTERM 처리를 추가했습니다. 강제 종료·정전 후에는 잠금 파일이 남을 수 있습니다. **컨테이너가 완전히 중지된 경우에만** data의 해당 .app.lock을 제거한 뒤 재실행합니다.

## 검증 상태

배포 파일을 제공하지만 실제 Synology 및 Docker 이미지 빌드는 이 환경에서 검증하지 않았습니다. NAS 모델, DSM·Container Manager 버전, 폴더 권한과 DNS/LDAP 접근은 설치 환경에서 확인해야 합니다.
