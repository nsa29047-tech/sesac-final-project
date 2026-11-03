"""Supabase 의 public 스키마(식당·메뉴·청크·벡터 등)를 SQL 파일 하나로 내보낸다. Docker 스택의 db 컨테이너가 처음 만들어질 때 이 파일을 복원한다.

- pg_dump 는 postgres:17 이미지로 실행하므로 로컬에 PostgreSQL 클라이언트가 없어도 된다(Docker 필요).
- 접속 문자열은 .env 의 POSTGRES_URI 를 쓰고 출력하지 않는다. 문자열은 명령줄이 아니라 환경변수로 컨테이너에 넘긴다.
- Supabase 연결 풀러의 트랜잭션 모드(6543)는 pg_dump 에 맞지 않아 세션 모드(5432)로 바꿔 접속한다.
- 결과는 data/db_snapshot/public.sql (data/ 는 git 제외). 계정·권한(--no-owner --no-privileges)은 담지 않는다.

실행:  uv run python src/db/export_snapshot.py
"""
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "db_snapshot" / "public.sql"
PG_IMAGE = "postgres:17"  # 서버(Supabase)와 같거나 더 높은 메이저 버전이어야 한다


def session_mode_uri(uri: str) -> str:
    """풀러의 트랜잭션 모드(포트 6543)를 세션 모드(5432)로 바꾼다. 풀러가 아니면 그대로 둔다."""
    if "pooler.supabase.com" in uri and ":6543/" in uri:
        return uri.replace(":6543/", ":5432/", 1)
    return uri


def main() -> int:
    load_dotenv(ROOT / ".env")
    uri = os.environ.get("POSTGRES_URI")
    if not uri:
        print("POSTGRES_URI 가 없습니다(.env).", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "docker", "run", "--rm", "-e", "PGURI", PG_IMAGE,
        "sh", "-c",
        'pg_dump "$PGURI" --schema=public --no-owner --no-privileges --no-comments --format=plain',
    ]
    env = {**os.environ, "PGURI": session_mode_uri(uri)}
    tmp = OUT.with_suffix(".sql.part")
    with tmp.open("wb") as f:
        proc = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        # 오류 문구에 접속 문자열이 섞이지 않는지 확인하고 보여 준다
        err = proc.stderr.decode(errors="replace").strip().splitlines()
        print("pg_dump 실패:", *[line for line in err if uri not in line][-5:], sep="\n  ", file=sys.stderr)
        return proc.returncode

    tmp.replace(OUT)
    text = OUT.read_text(encoding="utf-8")
    copies = text.count("\nCOPY ")
    print(f"저장: {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024 / 1024:.1f} MB, 테이블 {copies}개 COPY)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
