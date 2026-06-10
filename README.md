# HAFS PrintQueue

HAFS 3D프린터 동아리용 출력 큐 관리 시스템. Bambu Lab A1 + AMS Lite 프린터의 작업 큐, 상태 모니터링, 필라멘트 관리를 자동화합니다.

## 주요 기능

- Google OAuth 로그인 (학교 도메인 제한)
- 학생 출력 신청 → 관리자 승인 → 큐 자동 관리
- AMS 슬롯 자동 매핑 / 관리자 슬롯 지정
- 실시간 진행률, 노즐·베드 온도, 카메라 스냅샷
- 월별 출력 리포트 (HTML / Excel)

## 기술 스택

FastAPI, SQLAlchemy (SQLite), Jinja2, Docker, `bambulabs_api` (MQTT)

## 설치

1. 저장소 클론
2. 환경변수 설정
3. Google OAuth 발급
   - Google Cloud Console에서 OAuth 2.0 클라이언트 ID 생성
   - 리다이렉트 URI: `https://<도메인>/auth/callback`
   - 발급된 ID/시크릿을 `.env`에 입력

4. 프린터 정보는 첫 관리자 로그인 후 웹 UI에서 등록 (IP, 액세스코드, 시리얼)
   - Bambu 프린터는 LAN 모드 + Developer Mode 활성화 필요

5. 실행
## 라이센스

학교 내부 프로젝트.
