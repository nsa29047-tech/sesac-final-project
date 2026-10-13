"""식당 분류 규칙. 세부 분류(category_detail)는 SQL로 조회하려면 한 식당에 하나만 있어야 해서 하나로 정리한다.

- 영상 추출값에 "고기 전문점, 삼겹살, 목살, 된장찌개"처럼 여러 개가 쉼표로 들어오면 첫 번째를 세부 분류로 쓰고 나머지는 태그로 옮긴다.
- "미쉐린 3스타"처럼 분류가 아닌 값은 세부 분류로 쓰지 않는다(미슐랭 여부는 michelin_status가 근거). 태그로만 남긴다.
- 사람이 확인해 정한 분류는 MANUAL_CATEGORY(google_cid -> (대분류, 세부 분류))가 우선한다. load_notes.py 가 다시 적재해도 유지된다.
- 대분류는 한식·일식·중식·양식·동남아식·인도식·중동식·기타 중 하나다(해산물 전문점처럼 나라로 나눌 수 없으면 기타).
"""
import re

BROAD = ("한식", "일식", "중식", "양식", "동남아식", "인도식", "중동식", "기타")
NOT_A_CATEGORY = re.compile(r"미쉐린|미슐랭|michelin|스타 레스토랑|별\s?\d", re.I)
# 미슐랭 값은 michelin_status 가 따로 관리하므로 세부 분류에도 태그에도 넣지 않는다("생면 파스타"의 "스타" 같은 말은 걸리지 않게 숫자+스타만 본다)
MICHELIN_TAG = re.compile(r"미쉐린|미슐랭|michelin|\d\s*스타|별\s?\d", re.I)

# google_cid -> (대분류, 세부 분류, 사유)
MANUAL_CATEGORY = {
    "3092251090469102902": ("중식", "면요리", "막스누들: 완탕면·에그누들 가게라 세부 분류는 면요리(LLM이 '중식'으로 정리해 수동 보정)"),
    "2510587266977636889": ("기타", "베이커리", "타이청 베이커리: 에그타르트 등 제과점이라 어떤 대분류에도 맞지 않아 기타(LLM 제안, 10/13)"),
    "2467554425010125486": ("기타", "해산물", "더형제 강남 논현점: 방어·회·해산물이 중심이라 일식이 아니라 기타/해산물(사용자 결정 10/13, 다른 해산물 식당과 표기 통일)"),
}


# google_cid -> 수동 분류와 맞지 않아 지울 태그. 태그의 '일식'이 남으면 일식 검색에 계속 걸린다
MANUAL_DROP_TAGS = {
    "2467554425010125486": ["일식", "일본식", "해산물 전문점"],
}

# 세부 분류 표기 예시(대분류별). 분류 프롬프트(pipeline/common.py CUISINE_PROMPT)와 pick_detail.py 가 함께 쓴다.
# 새 세부 분류가 늘면 여기에 추가한다. 표기가 같아야 SQL(cuisine 필터)로 찾을 수 있다.
VOCAB = """한식: 한정식, 고깃집, 국밥, 탕·찌개, 냉면·국수, 회, 해산물, 치킨, 백반, 사찰음식, 모던 한식, 한식 파인다이닝
일식: 오마카세, 스시, 가이세키, 소바, 라멘, 야키토리, 이자카야, 돈카츠, 징기스칸, 회
중식: 광둥식, 사천식, 딤섬, 면요리, 중식당
양식: 프렌치, 이탈리안, 스페인 요리, 바스크 요리, 타파스 바, 비스트로, 브라세리, 스테이크하우스, 파인다이닝, 와인 바, 해산물 레스토랑
기타: 해산물 등 나라로 나눌 수 없는 식당"""


# 같은 뜻의 표기를 하나로 통일한다(SQL 검색이 되려면 표기가 같아야 한다). 접미사 '전문점/전문 식당/전문'은 먼저 뗀다
SYNONYM = {
    "고깃집": ["고기", "갈비", "한우", "삼겹살", "고기 구이", "소고기", "돼지고기"],
    "국밥": ["돼지국밥", "소머리국밥", "순대국밥", "선지국밥"],
    "해산물": ["해산물 식당", "해산물 한식당", "해산물 요리"],
    "광둥식": ["광동식", "광둥 요리", "광동 요리", "광둥식 레스토랑", "광둥식 중식당", "광동식 레스토랑"],
    "사천식": ["사천 요리"],
    "중식당": ["고급 중식당", "모던 중식당"],
    "모던 한식": ["현대 한식", "현대적 한식", "모던 한식당", "모던 한식 다이닝"],
    "한식 파인다이닝": ["코리안 파인다이닝", "모던 한식 파인다이닝"],
    "프렌치": ["프랑스 요리", "프랑스 레스토랑", "프렌치 레스토랑", "모던 프렌치", "고급 프랑스 요리", "클래식 프랑스 요리"],
    "이탈리안": ["이탈리아 요리", "이탈리안 레스토랑", "전통 이탈리아 요리"],
    "스페인 요리": ["현대적인 스페인 요리"],
    "회": ["자연산 회", "물회", "활어회"],
}
_CANON = {alias: canon for canon, aliases in SYNONYM.items() for alias in aliases}


def canonical_detail(detail):
    """세부 분류 표기 통일. '고기 전문점' -> '고깃집', '광동식' -> '광둥식'."""
    if not detail:
        return detail
    d = re.sub(r"\s*(전문점|전문 식당|전문)$", "", detail.strip()) or detail.strip()
    return _CANON.get(d, d)


MICHELIN_WORDS = re.compile(r"(미쉐린|미슐랭|michelin)\s*(\d\s*스타|스타)?\s*", re.I)
GENERIC = {"", "레스토랑", "식당", "중식당", "한식당", "일식당", "양식당"}   # 대분류와 다를 바 없는 말은 세부 분류로 쓰지 않는다


def clean(item):
    """항목에서 미슐랭 문구만 뺀다. '미쉐린 2스타 프랑스 레스토랑' -> '프랑스 레스토랑'. 남는 것이 없거나 '레스토랑'뿐이면 None(분류가 아님)."""
    if not NOT_A_CATEGORY.search(item):
        return item
    rest = MICHELIN_WORDS.sub("", item).strip()
    return rest if rest not in GENERIC else None


def split_detail(detail):
    """세부 분류 문자열 -> (하나만 남긴 세부 분류 또는 None, 태그로 옮길 항목 목록)."""
    if not detail:
        return None, []
    items = [x.strip() for x in re.split(r"[,、，/]", detail) if x.strip()]
    cleaned = [(x, clean(x)) for x in items]
    keep = [c for _, c in cleaned if c]
    if not keep:
        return None, []
    return canonical_detail(keep[0])[:50], keep[1:]   # 미슐랭 문구는 태그로도 남기지 않는다


def finalize_cuisine(broad, detail, tags):
    """분류 결과의 마지막 정리(신규 데이터용). 세부 분류는 대표 하나(표기 통일), 나머지 후보는 태그로, 미슐랭·'null' 같은 값은 뺀다."""
    d, extras = split_detail(detail)
    out = []
    for t in [*(tags or []), *extras]:
        t = str(t).strip()
        if t and t.lower() not in ("null", "none") and not MICHELIN_TAG.search(t) and t != d and t not in out:
            out.append(t)
    return broad, d, out


def normalize(cid, broad, detail):
    """(대분류, 세부 분류, 태그로 옮길 항목, 적용한 수동 규칙 사유 또는 None)."""
    if cid in MANUAL_CATEGORY:
        b, d, why = MANUAL_CATEGORY[cid]
        # 원래 세부 분류의 내용은 잃지 않고 태그로 남긴다
        _, extras = split_detail(detail)
        old = [x.strip() for x in re.split(r"[,、，/]", detail or "") if x.strip()]
        return b, d, [x for x in dict.fromkeys(old + extras) if x != d and not MICHELIN_TAG.search(x)], why
    d, extras = split_detail(detail)
    return broad, d, extras, None
