#!/usr/bin/env bash
# db 컨테이너를 처음 만들 때(데이터 볼륨이 비어 있을 때) 한 번만 실행된다(docker-entrypoint-initdb.d).
# 이미 만들어진 볼륨에는 다시 실행되지 않으므로, 스냅샷을 바꿨으면 `docker compose down -v` 로 볼륨을 지우고 다시 올린다.
#
# 하는 일: pgvector 확장 -> 식당 데이터(스냅샷 복원) -> 읽기 전용 계정(챗봇) -> app 스키마와 계정(Spring)
set -euo pipefail

run_sql() { psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" "$@"; }

# 비밀번호는 SQL 문자열 안에 그대로 들어가므로 따옴표·이스케이프가 필요 없는 문자만 허용한다
require_password() {
  local name="$1" value="${!1:-}"
  if [[ ! "$value" =~ ^[A-Za-z0-9_-]{12,}$ ]]; then
    echo "[init] $name 은(는) 영문·숫자·_- 로 된 12자 이상이어야 합니다(docker/stack.env)" >&2
    exit 1
  fi
}
require_password READONLY_DB_PASSWORD
require_password APP_DB_PASSWORD

echo "[init] pgvector 확장"
run_sql -q -c 'CREATE EXTENSION IF NOT EXISTS vector;'

if [ -s /snapshot/public.sql ]; then
  echo "[init] Supabase 스냅샷 복원 (/snapshot/public.sql)"
  # pg_dump 출력에서 이 DB 에는 맞지 않는 줄만 뺀다: public 스키마는 이미 있고(CREATE SCHEMA 는 실패한다),
  # \restrict 는 psql 버전에 따라 모르는 명령이다(직접 만든 덤프라 필요 없다)
  sed -e '/^\\restrict /d' -e '/^\\unrestrict /d' -e '/^CREATE SCHEMA public;$/d' /snapshot/public.sql \
    | run_sql -q > /dev/null
else
  echo "[init] 경고: 스냅샷이 없어 restaurants 스키마만 만듭니다. 챗봇 검색은 제대로 동작하지 않습니다." >&2
  echo "[init]       uv run python src/db/export_snapshot.py 로 스냅샷을 만든 뒤 'docker compose down -v' 하고 다시 올리세요." >&2
  run_sql -q -f /sql/restaurant_schema.sql
fi

# 비밀번호 자리(플레이스홀더)만 바꿔서 기존 스크립트를 그대로 쓴다. 두 파일의 플레이스홀더 표기가 다르다(밑줄/공백).
echo "[init] 읽기 전용 계정 readonly_user (챗봇)"
sed "s/<여기에_비밀번호>/${READONLY_DB_PASSWORD}/g" /sql/create_readonly_role.sql | run_sql -q

echo "[init] app 스키마와 app_user (Spring)"
sed "s/<여기에 비밀번호>/${APP_DB_PASSWORD}/g" /sql/migrate_app_schema.sql | run_sql -q

echo "[init] 완료"
