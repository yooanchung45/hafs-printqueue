# HAFS PrintQueue

외대부고(HAFS) 3D 프린터 팜의 출력 신청, 승인, 대기열, 장비 상태를 관리하는 웹 애플리케이션입니다.

**By Jiu Yun & Yooan Chung & Siwon Choi**

## 구성

- `frontend/`: Next.js 16 App Router UI
- `backend/`: FastAPI JSON API, 프린터 통신, 슬라이서
- `data/`: SQLite DB, 출력 파일, 게시판 첨부 파일
- `docker-compose.yml`: 공통 서비스 정의
- `docker-compose.override.yml`: 로컬 개발 설정(자동 병합)
- `docker-compose.prod.yml`: 운영 전용 리버스 프록시 네트워크·폰트 마운트

```text
hafs-printqueue/
├── frontend/              # Next.js
│   ├── app/               # App Router 페이지
│   ├── components/
│   └── public/
├── backend/               # FastAPI
│   ├── routes/            # API 라우터
│   ├── slicer_profiles/
│   └── tests/
├── data/                  # 런타임 데이터(git 제외)
├── docker-compose.yml
├── docker-compose.override.yml
└── docker-compose.prod.yml
```

브라우저는 Next.js의 `3000` 포트에만 접속합니다. Next.js가 `/api/*` 요청을 FastAPI의 동일한 `/api/*` 경로로 프록시합니다. FastAPI는 HTML을 렌더링하지 않습니다.

## 주요 기능

### 학생

- 학교 Google 계정 로그인과 세션 유지
- STL 다중 업로드, Three.js 3D 미리보기, 축별 크기·90° 회전 조정
- 256 × 256 × 256 mm 베드 초과 경고와 PrusaSlicer 자동 슬라이싱
- `.3mf`·`.gcode.3mf` 직접 다중 업로드
- 스마트 프린터 선택, 작업 상태·미리보기 확인, 신청 취소
- 공지·질문·자유 게시판, 첨부 파일, 댓글과 답글
- 사용 가이드와 프린터 카메라 라이브뷰

### 관리자

- 신청 승인·거절, 프린터 재배정, 대기열 순서 조정
- AMS 슬롯 자동·수동 선택, FTPS 전송 진행률, 출력 시작·중단·완료·실패 처리
- 프린터 등록·수정·삭제, MQTT 상태 동기화, 조명과 카메라 제어
- 취소·실패 작업 복구·정리, 기간별 출력 통계와 Excel 다운로드

## 기술 스택

| 영역 | 기술 |
|---|---|
| 프런트엔드 | Next.js 16, React 19, TypeScript, Three.js, Lucide |
| 백엔드 | Python, FastAPI, SQLAlchemy async, SQLite |
| 인증 | Google OAuth 2.0, 서명 세션 쿠키 |
| 장비 연동 | Bambu Lab LAN/Developer Mode, MQTT, FTPS, LAN 카메라 |
| 슬라이싱 | PrusaSlicer CLI |
| 배포 | Docker Compose, Caddy 또는 Cloudflare Tunnel |

## 환경 변수

```bash
cp .env.example .env
```

최소 설정:

```dotenv
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
SESSION_SECRET=...
ALLOWED_EMAIL_DOMAIN=hafs.hs.kr
ADMIN_EMAILS=admin@example.com
OAUTH_REDIRECT_URI=http://localhost:3000/api/auth/callback
```

Google Cloud Console에도 정확히 같은 OAuth 리디렉션 URI를 등록합니다. 운영에서는 `https://<도메인>/api/auth/callback`을 사용합니다.

## Docker로 실행

로컬에서는 기본 설정과 override가 자동 병합됩니다.

```bash
docker compose up --build --remove-orphans
```

- 사이트: `http://localhost:3000`
- API 직접 확인: `http://localhost:8000/api/health`
- API 문서: `http://localhost:8000/docs`

운영에서는 공통 설정과 `docker-compose.prod.yml`을 함께 사용합니다. 호스트 포트는 노출하지 않고, 프런트엔드만 외부 `web` 네트워크에 연결됩니다.

```bash
docker network create web  # 이미 있으면 생략
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Caddy 등 리버스 프록시의 upstream은 `hafs-printqueue-web:3000`으로 지정합니다. `/api`도 Next.js를 통과하므로 별도 경로 분기는 필요하지 않습니다. FastAPI는 내부 기본 네트워크에만 연결됩니다. 프린터별 MQTT 연결은 프로세스 내부 singleton이므로 Uvicorn worker는 한 개만 사용합니다.

## Docker 없이 개발

백엔드:

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt  # Windows
.venv/Scripts/uvicorn main:app --reload --port 8000
```

프런트엔드:

```bash
cd frontend
corepack enable
pnpm install
pnpm dev
```

프런트엔드는 기본적으로 `http://127.0.0.1:8000`을 API origin으로 사용합니다. 다른 주소가 필요하면 서버 환경 변수 `API_ORIGIN`을 지정합니다.

## 프린터 연결

관리자 화면에서 프린터의 이름, IP, 시리얼, 액세스 코드를 등록합니다. Bambu A1에서 LAN Only Liveview와 Developer Mode를 활성화하고 서버가 장비의 TCP 6000, 8883, 990 포트에 접근할 수 있어야 합니다. 연결 정보가 없는 프린터는 로컬 UI 개발용 Mock 모드로 동작합니다.

## 라이선스

학교 내부 프로젝트 · 용인한국외국어대학교부설고등학교 메이커 시스템
