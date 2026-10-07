"""
같은 영상·같은 가게에 대해 세 가지 추출 방식을 돌려 결과를 나란히 비교한다.

- video     : Gemini가 영상(음성+화면)만 보고 추출. data/video_notes/{모델}/ 에 이미 있는 결과를 그대로 쓴다(추가 호출 없음).
- transcript: gpt-4o-mini가 자막(data/transcripts/{video_id}.txt)만 보고 추출.
- hybrid    : Gemini가 영상과 자막 텍스트를 함께 보고 추출. 자막은 상호·메뉴명 표기 교정과 말로 한 정보 보강에 쓴다.

세 방식 모두 같은 스키마(VideoResult/StoreNotes)로 가게 단위 결과를 만들고,
data/compare/{방식}/{video_id}__{google_cid}.json 에 건별 저장한다(이미 있으면 건너뛰므로 이어서 실행 가능).
비교 리포트는 data/compare/report.md 로 쓴다.

대상 영상은 --video-id 로 지정하거나, 지정하지 않으면 영상 결과와 자막이 모두 있는 영상 중 앞에서 --limit 개를 쓴다.
외부 API 호출 건수는 실행 전에 출력하며, --dry-run 이면 호출 없이 계획만 보여 준다.

실행 예:
  uv run python src/pipeline/compare_extraction_methods.py --dry-run
  uv run python src/pipeline/compare_extraction_methods.py --limit 3
  uv run python src/pipeline/compare_extraction_methods.py --video-id 3vYwR8V8IaU --video-id 7L_UMisgqrw
  uv run python src/pipeline/compare_extraction_methods.py --methods transcript_v2   # 자막 v2만(저렴)
  uv run python src/pipeline/compare_extraction_methods.py --report-only
"""

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from google.genai import types
from openai import OpenAI

from extract_transcript_notes import SYSTEM_PROMPT as TRANSCRIPT_PROMPT, TranscriptNotes
from extract_video_notes import (
    DEFAULT_MODEL as GEMINI_MODEL,
    DEFAULT_STORES_CSV,
    SYSTEM_PROMPT as VIDEO_PROMPT,
    VideoResult,
    call_with_retry,
    describe,
    load_stores,
)

ROOT = Path(__file__).resolve().parents[2]
TRANSCRIPT_DIR = ROOT / "data" / "transcripts"
VIDEO_DIR = ROOT / "data" / "video_notes" / GEMINI_MODEL
COMPARE_DIR = ROOT / "data" / "compare"
OPENAI_MODEL = "gpt-4o-mini"
OPENAI_PRICE_PER_M = (0.15, 0.60)  # (입력, 출력) USD / 1M 토큰
METHODS = ("video", "transcript", "hybrid", "transcript_v2", "hybrid_v2")
DEFAULT_RUN_METHODS = ("transcript_v2", "hybrid_v2")  # v1은 이미 저장된 결과를 재사용한다

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# v1 자막 프롬프트의 문제: "자막에 명시된 것만, 추측 금지"가 분류·분위기·태그에까지 적용돼 모델이 값을 비우고,
# 한국어 자막이라는 이유로 양식·중식 식당을 일식으로 분류했다(10곳 중 6곳). 그래서 사실 정보(가격 등)와
# 요약·분류 정보를 구분해 지시하고, 분류 기준과 예시를 명시한다.
TRANSCRIPT_PROMPT_V2 = """너는 미식 유튜브 영상의 자막(STT 스크립트)에서 식당 정보를 구조화해 추출하는 분석가야.

[사실 정보: 자막에 나온 것만]
- 가격, 도수, 조리법, 재료, 영업·예약 정보는 자막에 나온 것만 쓴다. 없으면 null 또는 빈 목록으로 두고 추측하지 않는다.
- 메뉴마다 근거가 된 자막 문장을 evidence에 원문 그대로 남긴다.
- 자막에는 음성 인식 오타가 있다. 상호와 메뉴명은 [대상 가게] 정보와 문맥으로 보정하되 없는 내용을 만들지 않는다.
- 유튜버의 미식 표현(바삭함, 진한 육수 등)은 최대한 원래 표현을 살려 적는다.

[요약·분류 정보: 자막 내용을 종합해서 채운다]
- category_broad는 식당이 실제로 내는 음식의 나라·계통으로 정한다. 자막이 한국어여도 한식이 아니다. 기준은 아래와 같다.
  한식=한국 요리 / 일식=일본 요리(스시, 라멘, 이자카야 등) / 중식=중국·홍콩·대만 요리(광둥, 딤섬, 완탕면 등)
  양식=유럽·아메리카 요리 전체(프랑스, 이탈리아, 스페인, 스테이크, 비스트로 등) / 동남아식 / 인도식 / 중동식 / 기타
  파리·밀라노 등 유럽 도시의 식당이나 파스타·스테이크·와인이 중심이면 양식이다. 홍콩 식당이면서 광둥 요리면 중식이다.
- category_detail은 메뉴와 식당 소개로 판단 가능하면 반드시 짧은 구로 채운다(예: 프랑스 가정식, 이탈리안, 완탕면, 베이커리, 파인다이닝). 정말 알 수 없을 때만 null.
- cuisine_tags는 자막에 나온 요리·조리 키워드로 3~5개를 채운다. 술, 와인, 재료 일반명은 넣지 않는다.
- atmosphere는 자막에서 언급된 공간, 좌석, 뷰, 손님층, 서비스, 주문·운영 방식을 1~2문장으로 요약한다. 언급이 전혀 없을 때만 null.
- cooking_features에는 유튜버가 설명한 재료, 조리법, 소스를 적는다.
- key_points는 이 식당만의 차별점과 방문 팁(예약, 웨이팅, 결제, 언어)이다. final_review는 유튜버가 마무리에서 한 평가를 요약한다.

[공통]
- 자막은 한 식당 또는 여러 식당을 소개한다. 사용자가 지정한 [대상 가게]의 정보만 추출하고, 다른 가게 정보와 섞지 않는다.
- 상호명은 자막이 아니라 [대상 가게]를 따른다. 인사말의 채널명(예: 비밀이야)은 식당명이 아니다.
- 메뉴는 구체적인 요리명만 넣는다. 파스타, 생선, 와인 같은 범주나 주류는 메뉴에서 제외한다(주류는 drinks에).
- 잡담, 인사, 촬영 뒷이야기, 구독 유도는 제외한다.
"""

HYBRID_PROMPT_V2_EXTRA = """
가격 규칙(하이브리드 v2):
- 메뉴판, 가격표, 계산서가 화면에 보이면 그 가격을 읽어 price에 반드시 적는다. 통화 단위를 포함한다(예: 31€, 1,200엔). 자막에 가격 언급이 없어도 화면에서 읽을 수 있으면 적는다.
- 가격이 화면에도 자막에도 없으면 null로 둔다. 메뉴 이름이 같아도 가격을 추측하지 않는다.
- 분류와 분위기는 화면과 자막을 종합해 채운다. 자막이 없는 장면(인테리어, 뷰)은 화면 근거로 쓴다.
"""

HYBRID_PROMPT = VIDEO_PROMPT + """
추가 규칙(하이브리드):
- 영상과 함께 자막(STT 스크립트) 전문이 주어진다. 자막은 음성 인식 오타가 많으므로, 상호·메뉴명·고유명사 표기는 [대상 가게] 정보와 화면(간판, 메뉴판)으로 교정한다.
- 말로 설명한 조리법, 맛 표현, 팁은 자막에서 찾고, 인테리어·음식 비주얼·가격표는 화면에서 찾아 서로 보완한다.
- 자막과 화면이 충돌하면 화면 근거를 우선하고 evidence에 그 사실을 적는다.
- evidence에 자막 근거는 원문 그대로, 화면 근거는 '(화면) ...' 형식으로 쓴다.
"""
HYBRID_PROMPT_V2 = HYBRID_PROMPT + HYBRID_PROMPT_V2_EXTRA

PROMPTS = {
    "transcript": TRANSCRIPT_PROMPT,
    "transcript_v2": TRANSCRIPT_PROMPT_V2,
    "hybrid": HYBRID_PROMPT,
    "hybrid_v2": HYBRID_PROMPT_V2,
}


def store_path(method: str, video_id: str, cid: str) -> Path:
    if method == "video":
        return VIDEO_DIR / f"{video_id}__{cid}.json"
    return COMPARE_DIR / method / f"{video_id}__{cid}.json"


def read_transcript(video_id: str) -> Optional[str]:
    path = TRANSCRIPT_DIR / f"{video_id}.txt"
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    return text or None


def save(method: str, video_id: str, store: dict, notes: dict, usage: dict) -> None:
    cid = store["google_cid"].strip()
    data = {
        "video_id": video_id,
        "google_cid": cid,
        "korean_name": store["korean_name"],
        "method": method,
        "usage": usage,
        **notes,
    }
    path = store_path(method, video_id, cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_transcript(method: str, video_id: str, store: dict, others: List[dict], text: str) -> None:
    """자막 방식: 가게 1곳당 gpt-4o-mini 1회 호출."""
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    started = time.time()
    response = openai_client.beta.chat.completions.parse(
        model=OPENAI_MODEL,
        temperature=0.1,
        messages=[
            {"role": "system", "content": PROMPTS[method]},
            {
                "role": "user",
                "content": (
                    f"[대상 가게]\n- {describe(store)}\n\n"
                    f"[같은 영상의 다른 가게 (추출 대상 아님, 정보를 섞지 말 것)]\n{others_text}\n\n"
                    f"다음 자막에서 [대상 가게]의 정보만 추출해줘. 영상에 가게가 여러 곳이면 대상 가게 부분만 다룬다:\n\n{text}"
                ),
            },
        ],
        response_format=TranscriptNotes,
    )
    notes = response.choices[0].message.parsed
    usage = {
        "prompt_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
        "seconds": round(time.time() - started, 1),
        "stores_in_call": 1,
    }
    save(method, video_id, store, notes.model_dump(), usage)


def run_hybrid(method: str, video_id: str, targets: List[dict], others: List[dict], text: str) -> None:
    """하이브리드 방식: 영상 1개당 Gemini 1회 호출(영상 + 자막 텍스트)."""
    targets_text = "\n".join(f"- google_cid={t['google_cid'].strip()} / {describe(t)}" for t in targets)
    others_text = "\n".join(f"- {describe(o)}" for o in others) or "(없음)"
    url = f"https://www.youtube.com/watch?v={video_id}"
    contents = types.Content(
        parts=[
            types.Part(file_data=types.FileData(file_uri=url)),
            types.Part(
                text=(
                    f"[대상 가게]\n{targets_text}\n\n"
                    f"[같은 영상의 다른 가게 (추출 대상 아님, 정보를 섞지 말 것)]\n{others_text}\n\n"
                    f"[자막 전문 (STT, 오타 있음)]\n{text}\n\n"
                    "이 영상과 자막을 함께 보고 [대상 가게] 각각의 정보를 추출해줘."
                )
            ),
        ]
    )
    config = types.GenerateContentConfig(
        system_instruction=PROMPTS[method],
        temperature=0.1,
        response_mime_type="application/json",
        response_schema=VideoResult,
    )
    started = time.time()
    response = call_with_retry(GEMINI_MODEL, contents, config)
    result: VideoResult = response.parsed
    if result is None:
        raise ValueError("구조화 응답 파싱 실패")
    usage = {
        "prompt_tokens": response.usage_metadata.prompt_token_count,
        "output_tokens": response.usage_metadata.candidates_token_count,
        "seconds": round(time.time() - started, 1),
        "stores_in_call": len(targets),
    }
    by_cid = {}
    for notes in result.stores:
        by_cid.setdefault(notes.google_cid.strip(), notes)
    for store in targets:
        notes = by_cid.get(store["google_cid"].strip())
        if notes is None:
            print(f"  [fail] {method} {video_id} / {store['korean_name']}: 응답에 가게 항목 없음")
            continue
        save(method, video_id, store, notes.model_dump(exclude={"google_cid"}), usage)


def pick_videos(by_video: Dict[str, List[dict]], video_ids: List[str], limit: int) -> List[str]:
    if video_ids:
        return video_ids
    picked = []
    for vid, stores in sorted(by_video.items()):
        cids = [s["google_cid"].strip() for s in stores if s["google_cid"].strip()]
        if cids and read_transcript(vid) and all(store_path("video", vid, c).exists() for c in cids):
            picked.append(vid)
    return picked[:limit]


def targets_of(stores: List[dict]) -> List[dict]:
    seen, out = set(), []
    for s in stores:
        cid = s["google_cid"].strip()
        if cid and cid not in seen:
            seen.add(cid)
            out.append(s)
    return out


def run_all(by_video: Dict[str, List[dict]], videos: List[str], run_methods: List[str], dry_run: bool) -> None:
    """방식별로 아직 결과가 없는 가게만 호출한다. transcript*는 가게 단위, hybrid*는 영상 단위 호출."""
    plan: Dict[str, list] = {m: [] for m in run_methods}
    for vid in videos:
        targets = targets_of(by_video.get(vid, []))
        if not read_transcript(vid):
            print(f"[skip] {vid}: 자막 없음")
            continue
        for s in targets:
            if not store_path("video", vid, s["google_cid"].strip()).exists():
                print(f"[warn] {vid} / {s['korean_name']}: 영상 방식 결과 없음(비교에서 video 열은 빈 값)")
        for m in run_methods:
            pending = [s for s in targets if not store_path(m, vid, s["google_cid"].strip()).exists()]
            if m.startswith("transcript"):
                plan[m] += [(vid, s) for s in pending]
            elif pending:
                plan[m].append(vid)

    print(f"대상 영상 {len(videos)}개 / 호출 계획: " + ", ".join(f"{m} {len(plan[m])}회" for m in run_methods))
    if dry_run:
        return

    for m in run_methods:
        for item in plan[m]:
            if m.startswith("transcript"):
                vid, store = item
                others = [s for s in by_video[vid] if s is not store]
                try:
                    run_transcript(m, vid, store, others, read_transcript(vid))
                    print(f"[ok] {m} {vid} / {store['korean_name']}")
                except Exception as e:
                    print(f"[fail] {m} {vid} / {store['korean_name']}: {e}")
            else:
                vid = item
                targets = [t for t in targets_of(by_video[vid]) if not store_path(m, vid, t["google_cid"].strip()).exists()]
                others = [s for s in by_video[vid] if s not in targets]
                try:
                    run_hybrid(m, vid, targets, others, read_transcript(vid))
                    print(f"[ok] {m} {vid} ({len(targets)}곳)")
                except Exception as e:
                    print(f"[fail] {m} {vid}: {e}")


# -------------------------------------------------------------
# 리포트
# -------------------------------------------------------------
def load_result(method: str, vid: str, cid: str) -> Optional[dict]:
    path = store_path(method, vid, cid)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def metrics(d: dict) -> Dict[str, object]:
    menus = d.get("menus") or []
    return {
        "메뉴 수": len(menus),
        "맛 표현 있는 메뉴": sum(1 for m in menus if m.get("taste_review")),
        "조리 특징 있는 메뉴": sum(1 for m in menus if m.get("cooking_features")),
        "가격 있는 메뉴": sum(1 for m in menus if m.get("price")),
        "화면 첫 등장 시각 있는 메뉴": sum(1 for m in menus if m.get("first_appearance")),
        "근거 evidence 있는 메뉴": sum(1 for m in menus if m.get("evidence")),
        "음료 수": len(d.get("drinks") or []),
        "태그 수": len(d.get("cuisine_tags") or []),
        "key_points 수": len(d.get("key_points") or []),
        "분위기 글자 수": len(d.get("atmosphere") or ""),
        "총평 글자 수": len(d.get("final_review") or ""),
        "embedding_text 글자 수": len(d.get("embedding_text") or ""),
    }


def usage_line(d: dict, method: str) -> str:
    u = d.get("usage") or {}
    if not u:
        return "-"
    base = f"입력 {u.get('prompt_tokens', 0):,} / 출력 {u.get('output_tokens', 0):,}토큰, {u.get('seconds', '?')}초"
    if method.startswith("transcript"):
        cost = (u["prompt_tokens"] * OPENAI_PRICE_PER_M[0] + u["output_tokens"] * OPENAI_PRICE_PER_M[1]) / 1e6
        base += f", 약 ${cost:.4f}"
    if u.get("stores_in_call", 1) > 1:
        base += f" (호출 1회에 가게 {u['stores_in_call']}곳 공유)"
    return base


def bullet(items: List[str]) -> str:
    return "<br>".join(items) if items else "(없음)"


def build_report(by_video: Dict[str, List[dict]], videos: List[str]) -> str:
    lines = ["# 추출 방식 비교 리포트 (" + " / ".join(METHODS) + ")", ""]
    totals = {m: {k: 0 for k in metrics({})} for m in METHODS}
    counted = {m: 0 for m in METHODS}

    for vid in videos:
        for store in targets_of(by_video.get(vid, [])):
            cid = store["google_cid"].strip()
            res = {m: load_result(m, vid, cid) for m in METHODS}
            if not any(res.values()):
                continue
            lines += [f"## {store['korean_name']}  (`{vid}`)", f"영상 제목: {store.get('video_title', '')}", ""]

            lines += ["| 항목 | " + " | ".join(METHODS) + " |", "|" + "---|" * (len(METHODS) + 1)]
            ms = {m: metrics(r) if r else None for m, r in res.items()}
            for key in metrics({}):
                row = [str(ms[m][key]) if ms[m] else "-" for m in METHODS]
                lines.append(f"| {key} | " + " | ".join(row) + " |")
            lines.append("| 토큰·시간·비용 | " + " | ".join(usage_line(res[m], m) if res[m] else "-" for m in METHODS) + " |")
            lines.append("")
            for m in METHODS:
                if res[m]:
                    counted[m] += 1
                    for k, v in ms[m].items():
                        totals[m][k] += v

            def col(fn):
                return " | ".join(bullet(fn(res[m])) if res[m] else "-" for m in METHODS)

            lines += ["| 내용 | " + " | ".join(METHODS) + " |", "|" + "---|" * (len(METHODS) + 1)]
            lines.append("| 메뉴명 | " + col(lambda d: [m["name"] for m in d.get("menus") or []]) + " |")
            lines.append("| 분류 | " + col(lambda d: [f"{d.get('category_broad')}/{d.get('category_detail')}", ", ".join(d.get("cuisine_tags") or [])]) + " |")
            lines.append("| 분위기 | " + col(lambda d: [d.get("atmosphere") or "(없음)"]) + " |")
            lines.append("| key_points | " + col(lambda d: d.get("key_points") or []) + " |")
            lines.append("| 총평 | " + col(lambda d: [d.get("final_review") or "(없음)"]) + " |")
            lines.append("")

    lines += ["## 전체 합계 (비교 가능한 가게 수: " + ", ".join(f"{m} {counted[m]}" for m in METHODS) + ")", "", "| 항목 | " + " | ".join(METHODS) + " |", "|" + "---|" * (len(METHODS) + 1)]
    for key in metrics({}):
        lines.append(f"| {key} | " + " | ".join(str(totals[m][key]) if counted[m] else "-" for m in METHODS) + " |")
    lines.append("")
    lines.append("개수가 많다고 정확한 것은 아니다. 메뉴명·분위기 표를 보고, 모르는 영상은 직접 확인해 환각과 누락을 판단한다.")
    return "\n".join(lines)


def main(stores_csv: Path, video_ids: List[str], limit: int, run_methods: List[str], dry_run: bool, report_only: bool) -> None:
    by_video = load_stores(stores_csv)
    videos = pick_videos(by_video, video_ids, limit)
    print(f"비교 대상 영상: {videos}")
    if not report_only:
        run_all(by_video, videos, run_methods, dry_run)
    if dry_run:
        return
    COMPARE_DIR.mkdir(parents=True, exist_ok=True)
    report_path = COMPARE_DIR / "report.md"
    report_path.write_text(build_report(by_video, videos), encoding="utf-8")
    print(f"리포트 저장: {report_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="영상 / 자막 / 하이브리드 추출 방식을 같은 영상으로 비교한다.")
    ap.add_argument("--stores-csv", type=Path, default=DEFAULT_STORES_CSV)
    ap.add_argument("--video-id", action="append", default=[], help="비교할 영상 ID (여러 번 지정 가능)")
    ap.add_argument("--limit", type=int, default=5, help="--video-id 가 없을 때 영상 수 (기본 5)")
    ap.add_argument("--methods", nargs="+", choices=[m for m in METHODS if m != "video"], default=list(DEFAULT_RUN_METHODS),
                    help=f"이번에 호출할 방식 (기본 {' '.join(DEFAULT_RUN_METHODS)}). 이미 결과가 있는 가게는 건너뛴다")
    ap.add_argument("--dry-run", action="store_true", help="API 호출 없이 계획만 출력")
    ap.add_argument("--report-only", action="store_true", help="호출 없이 저장된 결과로 리포트만 다시 만든다")
    args = ap.parse_args()
    main(args.stores_csv, args.video_id, args.limit, args.methods, args.dry_run, args.report_only)
