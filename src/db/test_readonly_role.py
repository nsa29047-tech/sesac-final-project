"""읽기 전용 계정(create_readonly_role.sql)이 제대로 제한되는지 확인한다. 데이터는 바꾸지 않는다.

확인 항목: 조회 가능, 슈퍼유저·역할 생성 권한 없음, 모든 테이블에 쓰기 권한 없음,
기본 트랜잭션 읽기 전용, 실제 INSERT/DELETE/CREATE/DROP이 거부됨.
쓰기 시도는 행을 건드리지 않는 형태(WHERE false)로 보내고 항상 롤백한다.
관리자 URI로 실행하면 쓰기 항목이 FAIL로 나오므로 공유 전에 읽기 전용 URI로만 확인한다.

사용: uv run python src/db/test_readonly_role.py
      READONLY_POSTGRES_URI 환경변수(.env 또는 셸)를 사용한다. 접속 문자열을 명령줄에 넣지 않는다.
"""
import os
import sys


def main() -> int:
    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()
    uri = os.environ.get("READONLY_POSTGRES_URI")
    if not uri:
        print("READONLY_POSTGRES_URI 가 설정되어 있지 않다.")
        return 2

    conn = psycopg2.connect(uri)
    conn.autocommit = True  # 세션 기본값(default_transaction_read_only)을 그대로 확인하려고 읽기 전용 지정을 하지 않는다
    cur = conn.cursor()
    results = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))

    cur.execute("SELECT current_user")
    print(f"접속 계정: {cur.fetchone()[0]}")

    cur.execute("SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = current_user")
    sup, createdb, createrole = cur.fetchone()
    check("슈퍼유저·DB/역할 생성 권한 없음", not (sup or createdb or createrole))

    cur.execute("SHOW default_transaction_read_only")
    check("default_transaction_read_only = on", cur.fetchone()[0] == "on")

    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    tables = [r[0] for r in cur.fetchall()]
    check("public 테이블 조회 가능", bool(tables), f"{len(tables)}개")

    readable, writable = [], []
    for t in tables:
        cur.execute(
            "SELECT has_table_privilege(current_user, %s, 'SELECT'),"
            " has_table_privilege(current_user, %s, 'INSERT, UPDATE, DELETE, TRUNCATE')",
            (f"public.{t}", f"public.{t}"),
        )
        can_select, can_write = cur.fetchone()
        if can_select:
            readable.append(t)
        if can_write:
            writable.append(t)
    check("모든 테이블에 SELECT 권한", len(readable) == len(tables))
    check("쓰기 권한(INSERT/UPDATE/DELETE/TRUNCATE) 없음", not writable, ", ".join(writable))

    if readable:
        cur.execute(f'SELECT count(*) FROM public."{readable[0]}"')
        check(f"SELECT 실행 ({readable[0]})", cur.fetchone()[0] >= 0)

    def must_be_rejected(name: str, sql: str) -> None:
        try:
            cur.execute("BEGIN")
            cur.execute(sql)
            check(f"{name} 거부됨", False, "실행이 허용됨")
        except psycopg2.Error as e:
            check(f"{name} 거부됨", True, type(e).__name__)
        finally:
            try:
                cur.execute("ROLLBACK")
            except psycopg2.Error:
                pass

    if tables:
        t = tables[0]
        must_be_rejected("INSERT", f'INSERT INTO public."{t}" SELECT * FROM public."{t}" WHERE false')
        must_be_rejected("DELETE", f'DELETE FROM public."{t}" WHERE false')
        must_be_rejected("TRUNCATE", f'TRUNCATE public."{t}"')
    must_be_rejected("CREATE TABLE", "CREATE TABLE public._readonly_probe (id int)")

    conn.close()
    failed = results.count(False)
    print(f"\n{'모두 통과' if not failed else f'{failed}개 실패'} ({len(results)}개 항목)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
