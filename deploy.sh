#!/usr/bin/env bash
set -Eeuo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

readonly COMPOSE_FILE="docker-compose.yml"
readonly PROD_COMPOSE_FILE="docker-compose.prod.yml"
readonly REVISION_FILE="data/.deployed-revision"

compose() {
    docker compose -f "$COMPOSE_FILE" -f "$PROD_COMPOSE_FILE" "$@"
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

trap 'echo "ERROR: 배포 실패 (line ${LINENO})" >&2' ERR

echo "=== 배포 시작: $(date) ==="

command -v git >/dev/null 2>&1 || fail "git을 찾을 수 없습니다."
command -v docker >/dev/null 2>&1 || fail "docker를 찾을 수 없습니다."
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2를 사용할 수 없습니다."

[[ -f .env ]] || fail ".env 파일이 없습니다. .env.example을 참고해 운영 설정을 작성하세요."
[[ -f "$COMPOSE_FILE" && -f "$PROD_COMPOSE_FILE" ]] || fail "운영 Compose 파일이 없습니다."

# 런타임 데이터 디렉터리는 Git과 Docker 이미지 밖에 유지합니다.
mkdir -p data/uploads data/board

# 이전 단일 앱 구조의 게시판 첨부 파일을 1회 마이그레이션합니다.
if [[ -d app/static/board ]]; then
    cp -an app/static/board/. data/board/
fi

# 운영 프런트엔드가 연결될 외부 프록시 네트워크를 보장합니다.
if ! docker network inspect web >/dev/null 2>&1; then
    echo ">> Docker 네트워크 'web' 생성"
    docker network create web >/dev/null
fi

compose config --quiet

CURRENT_REVISION="$(git rev-parse HEAD)"
NEEDS_BUILD=false

if [[ ! -s "$REVISION_FILE" ]]; then
    NEEDS_BUILD=true
else
    PREVIOUS_REVISION="$(<"$REVISION_FILE")"
    if ! git cat-file -e "${PREVIOUS_REVISION}^{commit}" 2>/dev/null; then
        NEEDS_BUILD=true
    else
        CHANGED_FILES="$(git diff --name-only "$PREVIOUS_REVISION" "$CURRENT_REVISION")"
        if grep -qE '^(backend/|frontend/|docker-compose(\.prod)?\.yml$)' <<<"$CHANGED_FILES"; then
            NEEDS_BUILD=true
        fi
    fi
fi

if [[ "$NEEDS_BUILD" == true ]]; then
    echo ">> 애플리케이션 이미지 빌드 및 컨테이너 반영"
    compose up -d --build --remove-orphans
else
    echo ">> 이미지 변경 없음: Compose 설정 및 컨테이너 상태 반영"
    compose up -d --remove-orphans
fi

# 성공한 배포만 기준 커밋으로 기록합니다. 다음 배포의 변경 감지에 사용됩니다.
printf '%s\n' "$CURRENT_REVISION" > "$REVISION_FILE"

compose ps
echo "=== 배포 완료: $(date) / ${CURRENT_REVISION:0:12} ==="
