"""
filtered_channel_video_urls.txt 중 지정한 날짜(기본 2025-01-01) 이후에 올라온 영상만 골라 한국어 자막(스크립트)을 수집한다.

- 영상 목록이 최신순이라는 점을 이용해 collect_video_urls.find_cutoff_index 로 이분 탐색한다.
  업로드일 조회는 전체가 아니라 log2(N)회만 한다. 대상 ID는 data/urls/transcript_targets_{since}.txt 에 저장한다.
- 1단계 필터(소개란에 '가게'/'장소' 단어)는 이 채널의 소개란 템플릿 때문에 호텔 후기 등도 통과시킨다.
  그래서 restaurants_info.csv 에 가게와 google_cid 가 있는 영상(DB 적재 가능)만 대상으로 삼고,
  제외된 영상은 data/urls/transcript_excluded_{since}.txt 에 사유와 함께 남긴다. --all 이면 이 조건을 끈다.
- 자막은 yt-dlp로 받는다. get_transcripts.py(Playwright)는 YouTube가 자막 패널 구조를 바꿨고(transcript-segment-view-model),
  제작자가 영어 자막만 올린 영상은 패널이 영어로 열려서 쓰지 않는다.
- 자막 선택 우선순위: 제작자가 올린 한국어 자막(ko) > 자동 생성 한국어(ko-orig, 없으면 ko).
  자동 생성 자막은 음성 인식 오류(예: 상호명)가 많으므로 어느 쪽을 썼는지 data/transcripts/_sources.csv 에 기록한다.
- 결과는 영상마다 두 파일로 건별 저장한다.
    data/transcripts/{video_id}.txt   한 줄 = 자막 한 구간(시간 없음). 기존 추출 코드가 읽는 형식이며, 이미 있으면 덮어쓰지 않는다.
    data/transcripts/{video_id}.json  구간별 {start, duration, text}(초 단위)와 자막 출처. 메뉴가 영상의 어느 시점에 나오는지 찾는 데 쓴다.
  둘 다 있는 영상은 건너뛰므로 중단 후 재실행하면 이어서 처리된다. 자막이 없는 영상은 data/transcripts/_failed.txt 에 기록한다.
- 모델에 시각 자막을 넣을 때는 load_timed_transcript(video_id)가 "[00:03:52] 텍스트" 형식의 문자열을 만들어 준다.

실행 예:
  uv run python src/pipeline/get_transcripts_since.py --dry-run          # 대상 영상 수만 확인
  uv run python src/pipeline/get_transcripts_since.py --limit 3          # 샘플 3개만 수집
  uv run python src/pipeline/get_transcripts_since.py                    # 전체(이어서 처리)
"""

import argparse
import csv
import json
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

import yt_dlp

from collect_video_urls import find_cutoff_index

ROOT = Path(__file__).resolve().parents[2]
URLS_DIR = ROOT / "data" / "urls"
INPUT_FILE = URLS_DIR / "filtered_channel_video_urls.txt"
STORES_CSV = ROOT / "data" / "restaurants_info.csv"
OUTPUT_DIR = ROOT / "data" / "transcripts"
FAILED_FILE = OUTPUT_DIR / "_failed.txt"
SOURCES_FILE = OUTPUT_DIR / "_sources.csv"
DEFAULT_SINCE = "20250101"
REQUEST_DELAY = 1.0
MARKER_ONLY = re.compile(r"^\[[^\]]*\]$")  # [음악], [박수] 같은 소리 표시 줄


def video_id_of(url: str) -> str:
    return re.search(r"v=([0-9A-Za-z_-]{11})", url).group(1)


def select_targets(since: str) -> List[str]:
    """필터링된 영상 목록(최신순)에서 since 이상 업로드된 영상 ID만 반환한다."""
    urls = [line.strip() for line in INPUT_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    entries = [{"id": video_id_of(u), "title": u} for u in urls]
    cutoff = find_cutoff_index(entries, since)
    return [e["id"] for e in entries[:cutoff]]


def loadable_video_ids(stores_csv: Path) -> Tuple[set, set]:
    """(가게와 google_cid가 있는 영상 ID, CSV에 가게 이름이라도 있는 영상 ID)."""
    loadable, named = set(), set()
    with open(stores_csv, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row["korean_name"].strip():
                vid = video_id_of(row["video_url"])
                named.add(vid)
                if row["google_cid"].strip():
                    loadable.add(vid)
    return loadable, named


def pick_track(info: dict) -> Optional[Tuple[str, str, str]]:
    """(source, 언어코드, json3 URL). 제작자 한국어 자막 > 자동 생성 한국어 순. 없으면 None."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    for source, tracks, langs in (("manual_ko", manual, ["ko"]), ("auto_ko", auto, ["ko-orig", "ko"])):
        for lang in langs:
            for fmt in tracks.get(lang, []):
                if fmt.get("ext") == "json3":
                    return source, lang, fmt["url"]
    return None


def json3_to_segments(raw: bytes) -> List[dict]:
    """json3 이벤트를 {start, duration, text}(초 단위) 목록으로 바꾼다. 길이 값이 없으면 다음 구간 시작까지로 계산한다."""
    segments = []
    for event in json.loads(raw).get("events", []):
        text = "".join(seg.get("utf8", "") for seg in event.get("segs", []) or []).replace("\n", " ").strip()
        text = re.sub(r"^>>\s*", "", text)
        if text and not MARKER_ONLY.match(text):
            duration_ms = event.get("dDurationMs")
            segments.append({"start": event["tStartMs"] / 1000, "duration": duration_ms / 1000 if duration_ms else None, "text": text})
    for cur, nxt in zip(segments, segments[1:]):
        if cur["duration"] is None:
            cur["duration"] = round(max(nxt["start"] - cur["start"], 0), 3)
    return segments


def fetch_one(ydl: yt_dlp.YoutubeDL, vid: str) -> Tuple[Optional[List[dict]], dict]:
    info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    meta = {
        "video_id": vid,
        "title": info.get("title", ""),
        "upload_date": info.get("upload_date", ""),
        "manual_langs": "|".join((info.get("subtitles") or {}).keys()),
    }
    track = pick_track(info)
    if track is None:
        return None, meta
    source, lang, url = track
    segments = json3_to_segments(ydl.urlopen(url).read())
    meta.update({"source": source, "lang": lang, "lines": len(segments)})
    return segments, meta


def format_timestamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def load_timed_transcript(video_id: str) -> str:
    """{video_id}.json 을 "[00:03:52] 텍스트" 줄바꿈 문자열로 만든다(LLM 입력용). 파일이 없으면 FileNotFoundError."""
    data = json.loads((OUTPUT_DIR / f"{video_id}.json").read_text(encoding="utf-8"))
    return "\n".join(f"[{format_timestamp(seg['start'])}] {seg['text']}" for seg in data["segments"])


def recorded_sources() -> set:
    if not SOURCES_FILE.exists():
        return set()
    with open(SOURCES_FILE, "r", encoding="utf-8-sig", newline="") as f:
        return {row["video_id"] for row in csv.DictReader(f)}


def append_source(meta: dict) -> None:
    new = not SOURCES_FILE.exists()
    with open(SOURCES_FILE, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["video_id", "upload_date", "source", "lang", "lines", "manual_langs", "title"], extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(meta)


def has_text(vid: str) -> bool:
    path = OUTPUT_DIR / f"{vid}.txt"
    return path.exists() and bool(path.read_text(encoding="utf-8").strip())


def has_timed(vid: str) -> bool:
    return (OUTPUT_DIR / f"{vid}.json").exists()


def main(since: str, limit: int, dry_run: bool, include_all: bool) -> None:
    targets = select_targets(since)
    if not include_all:
        loadable, named = loadable_video_ids(STORES_CSV)
        reasons = {
            v: "Places 매칭 실패(google_cid 없음)" if v in named else "가게 정보 없음(추출 0곳 또는 CSV에 행 없음)"
            for v in targets
            if v not in loadable
        }
        excluded_file = URLS_DIR / f"transcript_excluded_{since}.txt"
        excluded_file.write_text("".join(f"{v}\t{r}\n" for v, r in reasons.items()), encoding="utf-8")
        print(f"적재 불가 영상 {len(reasons)}개 제외 (목록: {excluded_file.name})")
        targets = [v for v in targets if v in loadable]
    target_file = URLS_DIR / f"transcript_targets_{since}.txt"
    target_file.write_text("\n".join(targets) + "\n", encoding="utf-8")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pending = [v for v in targets if not (has_text(v) and has_timed(v))]
    print(f"{since} 이후 영상 {len(targets)}개 중 자막(.txt) 또는 시각 자막(.json)이 아직 없는 영상 {len(pending)}개 (목록: {target_file.name})")
    if dry_run:
        return
    if limit:
        pending = pending[:limit]
    print(f"이번 실행에서 {len(pending)}개를 수집합니다.\n" + "-" * 50)

    ok, failed, known_sources = {"manual_ko": 0, "auto_ko": 0}, [], recorded_sources()
    opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        for idx, vid in enumerate(pending, 1):
            try:
                segments, meta = fetch_one(ydl, vid)
            except Exception as e:
                print(f"[{idx}/{len(pending)}] {vid} 실패(일시 오류일 수 있음, 재실행하면 다시 시도): {str(e)[:100]}")
                time.sleep(REQUEST_DELAY)
                continue
            if not segments:
                print(f"[{idx}/{len(pending)}] {vid} 한국어 자막 없음 (수동 자막 언어: {meta['manual_langs'] or '없음'})")
                failed.append(vid)
            else:
                lines = [seg["text"] for seg in segments]
                note = ""
                if has_text(vid):  # 기존 .txt(예전 Playwright 수집분 등)는 덮어쓰지 않고 새 자막과 얼마나 같은지만 알린다
                    old = set((OUTPUT_DIR / f"{vid}.txt").read_text(encoding="utf-8").splitlines())
                    note = f" (기존 .txt 유지, 새 구간 중 기존 줄과 같은 비율 {sum(t in old for t in lines) / len(lines):.0%})"
                else:
                    (OUTPUT_DIR / f"{vid}.txt").write_text("\n".join(lines), encoding="utf-8")
                payload = {"video_id": vid, "source": meta["source"], "lang": meta["lang"], "segments": segments}
                (OUTPUT_DIR / f"{vid}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
                if vid not in known_sources:
                    append_source(meta)
                ok[meta["source"]] += 1
                print(f"[{idx}/{len(pending)}] {vid} {meta['source']}({meta['lang']}) {len(lines)}구간{note}")
            time.sleep(REQUEST_DELAY)

    if failed:
        with open(FAILED_FILE, "a", encoding="utf-8") as f:
            f.write("\n".join(failed) + "\n")
    print("-" * 50)
    print(f"완료: 제작자 한국어 자막 {ok['manual_ko']}개, 자동 생성 {ok['auto_ko']}개, 자막 없음 {len(failed)}개")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="지정한 날짜 이후 영상의 한국어 자막을 수집한다.")
    ap.add_argument("--since", default=DEFAULT_SINCE, help="이 날짜(YYYYMMDD) 이상 업로드된 영상만 (기본 20250101)")
    ap.add_argument("--limit", type=int, default=0, help="이번 실행에서 수집할 영상 수 (0이면 전부)")
    ap.add_argument("--all", action="store_true", help="적재 가능 여부와 상관없이 날짜 조건을 통과한 영상 전부 수집")
    ap.add_argument("--dry-run", action="store_true", help="대상 영상 수만 계산하고 수집은 하지 않는다")
    args = ap.parse_args()
    main(args.since, args.limit, args.dry_run, args.all)
