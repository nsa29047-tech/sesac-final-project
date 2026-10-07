"""자막 원문을 그대로 청킹하는 RAG 방식 실험. 식당 정보 추출(메뉴·분위기)을 거치지 않고 자막 자체를 벡터 검색 대상으로 쓴다.

- 영상에 식당이 1곳이면 전체 자막을 청크로 자른다.
- 식당이 여러 곳이면 LLM(gpt-4o-mini)이 식당별 구간의 시작 시각을 찾고, 구간별로 청크를 자른다.
  구간 경계는 data/transcript_chunks/_boundaries/{video_id}.json 에 저장해 재실행 시 다시 호출하지 않는다(--force 로 덮어씀).
- 청크 메타데이터: 식당 이름·주소·국가·지역(restaurants_info.csv, restaurant_regions.csv의 Places 결과), 영상 제목·URL, 자막 구간 시각.
  임베딩 대상은 자막 텍스트만이고 식당 이름은 넣지 않는다(식당은 메타데이터로 먼저 좁히는 설계, CLAUDE.md 아키텍처 원칙).
- DB에는 쓰지 않는다. 결과는 data/transcript_chunks/chunks.jsonl, embeddings.npy 로 저장한다.

사용:
  uv run python src/pipeline/chunk_transcripts.py                  # 청크 생성 + 임베딩
  uv run python src/pipeline/chunk_transcripts.py --dry-run        # 청크 통계만(임베딩·저장 없음, 경계 호출은 함)
  uv run python src/pipeline/chunk_transcripts.py --query "트러플 파스타" --top 5 [--country KR]
"""
import argparse
import csv
import json
import os
import re
from pathlib import Path
from typing import List, Optional

import numpy as np
from pydantic import BaseModel, Field

from extract_script_notes import MODEL, client, hms_to_sec, load_stores
from get_transcripts_since import OUTPUT_DIR as TRANSCRIPT_DIR, format_timestamp

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "transcript_chunks"
BOUNDARY_DIR = OUT_DIR / "_boundaries"
DESC_DIR = OUT_DIR / "_descriptions"  # 구간 나누기 힌트로 쓰는 영상 소개란(yt-dlp로 한 번만 받아 저장)
CHUNKS_FILE = OUT_DIR / "chunks.jsonl"  # main()에서 --chunk-chars 에 맞춰 chunks_{N}.jsonl 로 바뀐다
EMB_FILE = OUT_DIR / "embeddings.npy"
REGIONS_CSV = ROOT / "data" / "restaurant_regions.csv"
EMBED_MODEL = "text-embedding-3-small"
CHUNK_CHARS = 500     # 청크 목표 길이(글자)
OVERLAP_CHARS = 100   # 앞 청크와 겹치는 길이(글자). 줄 단위로 맞춘다


class Segment(BaseModel):
    store: int = Field(description="이 구간에서 소개하는 가게 번호(사용자가 준 번호 그대로)")
    start: str = Field(description="이 구간이 시작되는 자막 줄 앞의 [HH:MM:SS]를 그대로 복사한다(대괄호 제외)")


class Segments(BaseModel):
    segments: List[Segment] = Field(default_factory=list)


BOUNDARY_PROMPT = """너는 여러 식당을 소개하는 유튜브 영상의 자막을 식당별 구간으로 나누는 편집자야. 자막은 "[HH:MM:SS] 텍스트" 형식이고, 영상에서 소개하는 가게 목록이 번호와 함께 주어진다.

규칙:
- 영상이 한 가게를 소개하다가 다른 가게로 넘어가는 지점마다 구간을 나눈다. 구간은 시간 순서대로 적고, 각 구간에 그 구간에서 소개하는 가게 번호와 시작 시각을 적는다.
- 같은 가게가 나중에 다시 나오면 구간을 새로 만들어 같은 번호를 적는다.
- 시작 시각은 자막에 실제로 있는 [HH:MM:SS]를 그대로 복사한다. 계산하거나 지어내지 않는다.
- 영상 초반의 인사·전체 소개는 첫 번째 가게 구간에 포함한다. 가게가 바뀌는 지점은 새 가게를 처음 언급하거나 이동·입장하는 자막으로 본다.
- 목록의 모든 가게가 최소 한 번은 구간으로 나와야 한다.
- 영상 소개란이 함께 주어지면 챕터 시각("00:28 가게 이름")과 가게 정보를 힌트로 쓴다. 챕터 시각은 영상 편집 기준이라 자막 시각과 몇 초~수십 초 어긋날 수 있으니, 그 시각 근처의 자막에서 실제로 가게가 바뀌는 줄을 찾아 시작 시각으로 적는다. 소개란에 나온 가게 이름이 자막에서는 비슷한 발음이나 오타로 적혀 있을 수 있다.
- 가게가 아닌 챕터(출발, 이동, 숙소 등)는 바로 앞 가게 구간에 포함한다. 챕터에 없는 이동·이야기도 마찬가지다."""


def describe(store: dict) -> str:
    return " / ".join(p for p in (store["korean_name"], store["google_official_name"], store["address"]) if p)


def load_regions() -> dict:
    with open(REGIONS_CSV, "r", encoding="utf-8-sig", newline="") as f:
        return {r["google_cid"]: r for r in csv.DictReader(f)}


def get_description(vid: str) -> str:
    """영상 소개란. 처음 한 번만 yt-dlp로 받아 저장한다(API 비용 없음)."""
    path = DESC_DIR / f"{vid}.txt"
    if not path.exists():
        from extract_restaurant_info import get_youtube_description  # 식당 정보 추출 단계에서 쓴 것과 같은 함수
        DESC_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(get_youtube_description(f"https://www.youtube.com/watch?v={vid}")["description"], encoding="utf-8")
    return path.read_text(encoding="utf-8")


def norm(text: str) -> str:
    return re.sub(r"[\s\-_.,()\[\]]+", "", (text or "").lower())


def chapter_segments(description: str, stores: List[dict]) -> List[dict]:
    """소개란 챕터("MM:SS 제목")에서 가게별 구간을 만든다. 모든 가게가 챕터 제목에 있어야 하고, 하나라도 못 찾으면 None.
    가게가 아닌 챕터(출발, 이동, 숙소 등)는 새 구간을 만들지 않고 앞 가게 구간에 포함한다."""
    chapters = []
    for line in description.splitlines():
        m = re.match(r"\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s+(.+)$", line)
        if m:
            chapters.append((int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3]), m[4].strip()))
    segs = []
    for sec, title in sorted(chapters):
        for i, st in enumerate(stores):
            names = [norm(st["korean_name"]), norm(st["google_official_name"])]
            if any(n and (n in norm(title) or norm(title) in n) for n in names):
                if not segs or segs[-1]["store"] != i:
                    segs.append({"store": i, "start_sec": sec})
                break
    if {x["store"] for x in segs} != set(range(len(stores))):
        return None
    return segs


class StartLine(BaseModel):
    line: Optional[int] = Field(None, description="새 가게의 소개가 시작되는 첫 줄 번호(사용자가 준 번호 그대로). 모르겠으면 null")


REFINE_PROMPT = """너는 여러 식당을 소개하는 영상의 자막에서 가게가 바뀌는 지점을 정밀하게 찾는 편집자야. 이전 가게와 새 가게의 이름, 그리고 전환점 근처의 자막 줄이 번호와 함께 주어진다.
새 가게의 소개가 처음 시작되는 줄의 번호를 line 에 적는다.
- 가게 이름이 자막에 처음 나오는 줄이 아니라, 그 앞에서 새 가게로 넘어가는 말(이동·도착, 위치·역사·분위기 소개, 이전 가게 마무리 직후의 새 가게 이야기)이 시작되는 줄이다.
- 이전 가게를 마무리하는 말(총평, 다음 가게로 가자는 말)은 이전 가게 쪽이다. 새 가게 소개가 가게 이름 줄보다 뒤에 시작되면 이름 줄을 고른다.
- 날짜나 시간대가 바뀌는 말("2일차 아침", "점심으로 이동")과 새 장소로 이동·도착하는 말도 새 가게 구간의 시작이다. 이전 가게를 마무리하고 하루를 끝내는 말만 이전 가게 쪽이다.
- 판단이 어려우면 null 을 적는다."""
REFINE_BEFORE_SEC, REFINE_AFTER_SEC = 90, 10


def refine_starts(stores: List[dict], lines: List[dict], segs: List[dict], usage: list) -> List[dict]:
    """모델이 고른 구간 시작 시각은 가게 이름이 나오는 자막 줄인 경우가 많은데, 실제 소개는 그 앞 20~30초부터 시작한다(예: "구시가지에 있는, 서서 먹는 곳인데"
    다음에 이름 줄). 각 경계 앞 90초 안에서 새 가게 소개가 시작되는 줄을 다시 고르게 해 시작 시각을 앞으로 당긴다. 앞으로 당기는 경우만 받아들인다."""
    out = [dict(segs[0])]
    for prev, seg in zip(segs, segs[1:]):
        t = seg["start_sec"]
        cands = [l for l in lines if max(prev["start_sec"], t - REFINE_BEFORE_SEC) <= l["start"] <= t + REFINE_AFTER_SEC]
        new = dict(seg)
        if len(cands) >= 2:
            shown = "\n".join(f"{i}. [{format_timestamp(l['start'])}] {l['text']}" for i, l in enumerate(cands, 1))
            r = client.beta.chat.completions.parse(
                model=MODEL, temperature=0, seed=42, response_format=StartLine,
                messages=[{"role": "system", "content": REFINE_PROMPT},
                          {"role": "user", "content": f"[이전 가게]\n- {describe(stores[prev['store']])}\n\n[새 가게]\n- {describe(stores[seg['store']])}\n\n[자막 줄]\n{shown}"}])
            usage[0] += r.usage.prompt_tokens
            usage[1] += r.usage.completion_tokens
            n = r.choices[0].message.parsed.line
            if n and 1 <= n <= len(cands) and prev["start_sec"] < int(cands[n - 1]["start"]) <= t:
                new["start_sec"] = int(cands[n - 1]["start"])
        out.append(new)
    return out


def find_boundaries(vid: str, stores: List[dict], lines: List[dict], force: bool) -> List[dict]:
    """식당별 구간 [{store(0부터), start_sec}]. 모델 호출 결과를 저장해 재사용한다. 검증 실패하면 ValueError."""
    path = BOUNDARY_DIR / f"{vid}.json"
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))["segments"]

    description = get_description(vid)[:3000]
    segs = chapter_segments(description, stores)
    if segs:  # 소개란 챕터에 모든 가게가 있으면 모델 없이 그 시각을 쓴다
        BOUNDARY_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"video_id": vid, "method": "description_chapters", "segments": segs}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        return segs

    transcript = "\n".join(f"[{format_timestamp(l['start'])}] {l['text']}" for l in lines)
    store_list = "\n".join(f"{i}. {describe(s)}" for i, s in enumerate(stores, 1))
    resp = client.beta.chat.completions.parse(
        model=MODEL, temperature=0, seed=42, response_format=Segments,
        messages=[
            {"role": "system", "content": BOUNDARY_PROMPT},
            {"role": "user", "content": f"[가게 목록]\n{store_list}\n\n[영상 소개란]\n{description}\n\n[자막]\n{transcript}"},
        ],
    )
    parsed = resp.choices[0].message.parsed
    starts = {int(l["start"]) for l in lines}
    found = []
    for s in parsed.segments:
        sec = hms_to_sec(s.start)
        if sec is None or not 1 <= s.store <= len(stores):
            continue
        found.append((min(starts, key=lambda x: abs(x - sec)), s.store - 1))  # 모델이 적은 시각을 실제 자막 줄 시각에 맞춘다
    segs = []
    for sec, store in sorted(found):  # 모델이 시간순으로 안 적는 경우가 있어 시각순으로 다시 정렬한다
        if segs and (segs[-1]["store"] == store or segs[-1]["start_sec"] == sec):  # 같은 가게의 연속 구간은 합치고, 같은 시각이면 앞의 것을 둔다
            continue
        segs.append({"store": store, "start_sec": sec})
    missing = set(range(len(stores))) - {s["store"] for s in segs}
    if missing:
        raise ValueError(f"{vid}: 구간에 없는 가게 {sorted(missing)} (모델 응답 {len(parsed.segments)}개 중 유효 {len(segs)}개)")
    usage = [resp.usage.prompt_tokens, resp.usage.completion_tokens]
    segs = refine_starts(stores, lines, segs, usage)
    BOUNDARY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"video_id": vid, "method": "llm+refine", "segments": segs, "usage": usage}, ensure_ascii=False, indent=1), encoding="utf-8")
    return segs


MIN_SEGMENT_SEC = 30  # 이보다 짧은 구간은 가게 소개가 아니라 영상 끝 요약 같은 한두 줄이라 앞 구간에 합친다


def merge_short_segments(segs: List[dict], video_end: float) -> List[dict]:
    """너무 짧은 구간(예: 영상 끝에서 가게 이름을 한 줄씩 나열하는 요약)을 앞 구간에 합친다. 첫 구간은 합치지 않고, 합친 뒤 같은 가게가 연속되면 하나로 묶는다."""
    out = []
    for i, seg in enumerate(segs):
        end = segs[i + 1]["start_sec"] if i + 1 < len(segs) else video_end
        if out and end - seg["start_sec"] < MIN_SEGMENT_SEC:
            continue
        if out and out[-1]["store"] == seg["store"]:
            continue
        out.append(seg)
    return out


def split_lines(lines: List[dict]) -> List[dict]:
    """줄 단위로 모아 약 CHUNK_CHARS 글자의 청크로 자른다. 앞 청크 끝의 약 OVERLAP_CHARS 글자는 다음 청크 앞에 겹친다."""
    chunks, cur, size, fresh = [], [], 0, 0  # fresh: 직전 청크 이후 새로 들어온 줄 수(겹침 줄 제외)
    for l in lines:
        cur.append(l)
        fresh += 1
        size += len(l["text"]) + 1
        if size >= CHUNK_CHARS:
            chunks.append(cur)
            fresh = 0
            keep, k = [], 0
            for prev in reversed(cur):  # 겹치는 줄: 뒤에서부터 OVERLAP_CHARS 이상 채울 때까지
                if k >= OVERLAP_CHARS:
                    break
                keep.insert(0, prev)
                k += len(prev["text"]) + 1
            cur, size = keep, k
    if fresh:  # 겹침 줄만 남은 마지막 조각은 앞 청크에 이미 들어 있으므로 버린다
        if chunks and size < CHUNK_CHARS // 3:  # 새 내용이 너무 짧으면 앞 청크에 이어 붙인다
            chunks[-1] = chunks[-1] + cur[len(cur) - fresh:]
        else:
            chunks.append(cur)
    return [{"text": " ".join(x["text"] for x in c), "start_sec": int(c[0]["start"]),
             "end_sec": int(c[-1]["start"] + c[-1].get("duration", 0))} for c in chunks]


def video_parts(vid: str, stores: List[dict], lines: List[dict], force: bool = False) -> List[tuple]:
    """영상 자막을 식당별 구간으로 나눈다. [(가게 dict, 그 구간의 자막 줄들)]. 식당이 1곳이면 전체 자막, 여러 곳이면 구간 경계로 나눈다.
    정보 추출(extract_script_course.py)과 청킹이 같은 구간을 쓰도록 한 곳에서 만든다."""
    if len(stores) == 1:
        return [(stores[0], lines)]
    segs = merge_short_segments(find_boundaries(vid, stores, lines, force), lines[-1]["start"])
    parts = []
    for i, seg in enumerate(segs):
        end = segs[i + 1]["start_sec"] if i + 1 < len(segs) else float("inf")
        lo = 0 if i == 0 else seg["start_sec"]  # 첫 구간은 영상 처음부터 시작해 인사·전체 소개도 첫 가게에 포함한다
        parts.append((stores[seg["store"]], [l for l in lines if lo <= l["start"] < end]))
    return parts


def build_chunks(only_video: List[str], force: bool) -> List[dict]:
    by_video = load_stores()
    regions = load_regions()
    rows, failed = [], []
    for vid, stores in by_video.items():
        if only_video and vid not in only_video:
            continue
        data = json.loads((TRANSCRIPT_DIR / f"{vid}.json").read_text(encoding="utf-8"))
        lines = [s for s in data["segments"] if s["text"].strip()]
        if not lines:
            continue
        try:
            parts = video_parts(vid, stores, lines, force)
        except ValueError as e:
            failed.append(str(e))
            continue
        for store, part in parts:
            reg = regions.get(store["google_cid"].strip(), {})
            for c in split_lines(part):
                rows.append({
                    "video_id": vid, "video_title": store["video_title"], "video_url": store["video_url"],
                    "transcript_source": data["source"], "google_cid": store["google_cid"].strip(),
                    "name": store["korean_name"], "official_name": store["google_official_name"], "address": store["address"],
                    "country_code": store["country_code"], "region_1": reg.get("region_1", ""), "region_2": reg.get("region_2", ""),
                    "region_3": reg.get("region_3", ""), "multi_store_video": len(stores) > 1, **c,
                })
    for i, r in enumerate(rows):
        r["chunk_id"] = i
    for msg in failed:
        print(f"⚠️ 구간 나누기 실패, 건너뜀: {msg}")
    return rows


def embed(texts: List[str]) -> np.ndarray:
    vecs = []
    for i in range(0, len(texts), 64):
        resp = client.embeddings.create(model=EMBED_MODEL, input=texts[i:i + 64])
        vecs += [d.embedding for d in resp.data]
    m = np.array(vecs, dtype=np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)  # 정규화하면 내적이 코사인 유사도


def search(query: str, top: int, country: str, name: str) -> None:
    rows = [json.loads(l) for l in CHUNKS_FILE.read_text(encoding="utf-8").splitlines()]
    mat = np.load(EMB_FILE)
    q = embed([query])[0]
    sims = mat @ q
    order = [i for i in np.argsort(-sims)
             if (not country or rows[i]["country_code"] == country) and (not name or name in (rows[i]["name"] or ""))]
    print(f"질문: {query}" + (f" (country={country})" if country else "") + (f" (name={name})" if name else ""))
    for i in order[:top]:
        r = rows[i]
        print(f"\n[{sims[i]:.3f}] {r['name']} ({r['country_code']} {r['region_1']}) | {r['video_title'][:30]} | "
              f"{format_timestamp(r['start_sec'])}~{format_timestamp(r['end_sec'])}\n  {r['text'][:220]}")


def set_chunk_size(n: int) -> None:
    """청크 목표 길이를 바꾸고(겹침은 20%) 결과 파일 이름을 크기별로 나눈다. 크기를 바꿔 비교해도 서로 덮어쓰지 않는다."""
    global CHUNK_CHARS, OVERLAP_CHARS, CHUNKS_FILE, EMB_FILE
    CHUNK_CHARS, OVERLAP_CHARS = n, n // 5
    CHUNKS_FILE, EMB_FILE = OUT_DIR / f"chunks_{n}.jsonl", OUT_DIR / f"embeddings_{n}.npy"


def main():
    ap = argparse.ArgumentParser(description="자막 원문 청킹 + 임베딩 실험(DB 미사용)")
    ap.add_argument("--video-id", action="append", default=[], help="지정한 영상만 처리(여러 번 가능)")
    ap.add_argument("--force", action="store_true", help="저장된 구간 경계를 무시하고 다시 호출")
    ap.add_argument("--dry-run", action="store_true", help="청크 통계만 출력하고 임베딩·저장은 하지 않는다")
    ap.add_argument("--query", help="저장된 청크에서 검색할 질문")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--country", default="", help="검색 시 국가 코드로 먼저 좁힌다(예: KR)")
    ap.add_argument("--name", default="", help="검색 시 식당 이름(한국어 상호) 포함 여부로 좁힌다")
    ap.add_argument("--chunk-chars", type=int, default=500, help="청크 목표 길이(글자). 결과는 chunks_{N}.jsonl, embeddings_{N}.npy (기본 500)")
    args = ap.parse_args()
    set_chunk_size(args.chunk_chars)

    if args.query:
        search(args.query, args.top, args.country, args.name)
        return

    rows = build_chunks(args.video_id, args.force)
    lens = [len(r["text"]) for r in rows]
    print(f"청크 {len(rows)}개 (영상 {len({r['video_id'] for r in rows})}개, 식당 {len({r['google_cid'] for r in rows})}곳) "
          f"| 길이 평균 {int(np.mean(lens))} 최소 {min(lens)} 최대 {max(lens)}자 | 총 {sum(lens):,}자")
    if args.dry_run:
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mat = embed([r["text"] for r in rows])
    np.save(EMB_FILE, mat)
    CHUNKS_FILE.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    print(f"저장: {CHUNKS_FILE.relative_to(ROOT)}, {EMB_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
