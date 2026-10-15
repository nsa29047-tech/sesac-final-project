"""강 근처 검색 시험. 실행: uv run python src/chatbot/test_geoshape.py (Nominatim·DB 호출, 지명은 캐시된다)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
from chatbot.tools import FilterArgs, resolve_river, run_query  # noqa: E402

ok = True
f = FilterArgs(city="한강", region="서울")
assert resolve_river(f), "한강이 waterway로 조회되지 않음"
rows = run_query("SELECT COALESCE(name_ko, name_official) n FROM restaurants WHERE restaurant_id = ANY(%s)", (f._near_ids,))
names = [r["n"] for r in rows]
print("한강 1km 안 식당", len(names), names[:12])
for must in ("가겐", "밍글스"):
    hit = any(must in n for n in names)
    print(must, "포함" if hit else "누락")
    ok &= hit
f2 = FilterArgs(city="강남역")
is_river = resolve_river(f2)
print("강남역은 강이 아님:", not is_river)
ok &= not is_river
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
