# HAFS PrintQueue

외대부고 (HAFS) 3D프린터 팜 출력 관리 시스템.
Bambu Lab A1 + AMS Lite 프린터의 출력 신청, 큐 관리, 상태 모니터링을 자동화합니다.

**By Jiu Yun & Yooan Chung & Siwon Choi**

---

## 주요 기능

### 학생
- Google OAuth 로그인 (학교 도메인 `@hafs.hs.kr` 제한)
- **STL 파일 업로드** → 브라우저에서 3D 미리보기 (Three.js)
  - 크기 슬라이더 (1~400%)
  - X/Y/Z축 90° 회전 버튼
  - 베드 크기(256×256×256mm) 초과 경고
  - 여러 파일 동시 업로드 + 캐러셀로 각각 확인
- **서버 자동 슬라이싱** — STL → G-code 변환 (PrusaSlicer CLI)
- **.3mf / .gcode 직접 업로드** — 이미 슬라이싱된 파일 바로 제출
- 내 작업 목록 및 상태 확인

### 관리자
- 출력 신청 승인 / 거부
- 큐 관리 — 프린터별 작업 순서 자동 정렬
- 출력 시작 (AMS 슬롯 자동 탐색 또는 수동 지정)
- **출력 중단** — 프린터에 즉시 중단 명령 전송
- 완료 / 실패 처리
- 프린터 등록 및 정보 수정
- 프린터 상태 새로고침 (MQTT 동기화)
- 월별 출력 리포트 (Excel)

### 프린터 연동
- Bambu A1 LAN 모드 + Developer Mode 기반
- FTPS(포트 990)로 파일 업로드
- MQTT(포트 8883)로 출력 제어 및 상태 수신
- AMS 슬롯 자동 감지 및 DB 동기화
- `paho-mqtt` 게이트웨이가 프린터별 MQTT 연결 하나를 유지하고 상태·제어 명령을 통합
- **LAN 카메라 라이브뷰** — 시청자가 있을 때만 프린터당 TLS 연결 하나를 열고 여러 브라우저에 공유
  - 느린 클라이언트에는 지난 프레임을 버려 지연 누적 방지
  - 화면 밖/백그라운드 탭에서는 자동 중지, 장애 시 지수 백오프로 자동 복구

---

## 기술 스택

| 분야 | 기술 |
|---|---|
| 백엔드 | Python, FastAPI, SQLAlchemy (SQLite), Jinja2 |
| 프린터 통신 | `paho-mqtt` (상태·제어), `bambulabs_api` (임시 FTPS 업로드) |
| 슬라이싱 | PrusaSlicer CLI (Docker 내 설치) |
| 3D 뷰어 | Three.js (브라우저) |
| 인프라 | Docker, Caddy (리버스 프록시), Cloudflare Tunnel |
| 인증 | Google OAuth 2.0 |

---

## 프로젝트 구조

```
/srv/printqueue/
├── app/
│   ├── main.py              # FastAPI 진입점
│   ├── auth.py              # Google OAuth + 세션
│   ├── config.py            # 환경변수 설정
│   ├── db.py                # DB 연결
│   ├── models.py            # DB 모델 (User, Printer, Job, FilamentSlot)
│   ├── slicer.py            # PrusaSlicer CLI 래퍼
│   ├── stl_transform.py     # STL 스케일/회전 변환
│   ├── printer_client.py    # Bambu 프린터 통신
│   ├── printer_sync.py      # 프린터 상태 동기화
│   ├── reports.py           # Excel 리포트 생성
│   ├── routes/
│   │   ├── jobs.py          # 업로드, STL 미리보기, 작업 관리
│   │   └── admin.py         # 관리자 기능
│   ├── templates/           # Jinja2 HTML 템플릿
│   ├── static/              # CSS, 이미지
│   └── Dockerfile
├── data/
│   ├── db.sqlite            # SQLite 데이터베이스
│   └── uploads/             # 업로드된 파일 저장
├── .env                     # 환경변수 (비공개)
├── docker-compose.yml          # 프로덕션 설정 (서버 배포용)
└── docker-compose.override.yml # 로컬 개발용 오버라이드
```

---

## 설치 및 실행

### 사전 준비
- Raspberry Pi 5 (Ubuntu 24.04 aarch64)
- Docker + Docker Compose
- Tailscale (원격 접속용)
- Caddy + Cloudflare Tunnel (HTTPS 및 도메인)

### 1. 저장소 클론
```bash
git clone https://github.com/YOUR_USERNAME/hafs-printqueue.git
cd hafs-printqueue
```

### 2. 환경변수 설정
```bash
cp .env.example .env
nano .env
```

`.env` 필수 항목:
```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
SESSION_SECRET=...  # openssl rand -hex 32
ALLOWED_EMAIL_DOMAIN=hafs.hs.kr
ADMIN_EMAILS=your@email.com
OAUTH_REDIRECT_URI=https://your-domain/auth/callback
```

### 3. Google OAuth 설정
- Google Cloud Console에서 OAuth 2.0 클라이언트 ID 생성
- 승인된 리디렉션 URI: `https://<도메인>/auth/callback`

### 4. 실행 (프로덕션)
`docker-compose.yml`은 서버 배포용 설정입니다. Caddy 등 리버스 프록시와 공유하는 외부 네트워크(`web`)가 필요하며, 컨테이너 포트를 호스트에 직접 노출하지 않습니다.

```bash
# 외부 네트워크가 없다면 먼저 생성 (Caddy 스택에서 이미 만들었다면 생략)
docker network create web

docker compose up -d --build
```

로컬에 `docker-compose.override.yml`이 있으면 `docker compose` 명령이 자동으로 병합하므로, 서버에는 이 파일을 배포하지 않아야 합니다.

프린터별 MQTT singleton은 프로세스 내부에서 관리하므로 Uvicorn worker는 반드시 1개만 사용해야 합니다.

### 5. 프린터 등록
- 관리자 로그인 후 `/admin` 에서 프린터 추가
- Bambu A1에서 LAN 모드 + Developer Mode 활성화 필요
- IP, 시리얼 번호, 액세스 코드 입력
- 프린터 설정에서 **LAN Only Liveview**와 **Developer Mode** 활성화
- 서버에서 프린터의 TCP 6000 포트로 접근 가능한지 확인

---

## 개발 환경 (로컬)

`docker-compose.override.yml`이 `docker-compose.yml`과 같은 디렉토리에 있으면 `docker compose` 실행 시 자동으로 병합되어 로컬 개발용 설정이 적용됩니다.

- `8000:8000` 포트를 호스트에 노출
- `uvicorn --reload`로 코드 변경 시 자동 재시작
- `web` 네트워크를 로컬 브리지 네트워크로 생성 (외부 네트워크 불필요)

```bash
# .env에서 로컬 설정
OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback

# 실행 (docker-compose.yml + docker-compose.override.yml 자동 병합)
docker compose up
```

`http://localhost:8000` 에서 접속하며, 프린터 연결 없이도 Mock 모드로 UI 개발 가능.

---

## 라이센스

학교 내부 프로젝트 · 용인한국외국어대학교부설고등학교 메이커 시스템
