"""추출 결과 CSV에서 사람이 확인해야 할 식당만 뽑아 엑셀로 저장한다.

확인 대상:
  1. 미슐랭 등재 후보 (is_michelin=True): 매칭이 맞는지, 등급이 맞는지 확인
  2. Google 매칭 실패/불일치 (note 에 '사람이 확인 필요')
  3. 숙소(호텔 등)로 매칭된 식당: note 에 '숙소로 매칭됨'이 있는 행. 식당이 호텔로 잘못 매칭됐을 수 있음

사용: uv run python src/pipeline/export_review_list.py [입력 CSV] [출력 xlsx]
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "restaurants_info.csv"

REVIEW_COLUMNS = [
    "review_reason", "korean_name", "address", "google_official_name", "google_formatted_address",
    "google_maps_url", "latest_grade", "source_urls",
    "video_title", "video_url", "note",
]


def main(csv_path: Path, out_path: Path):
    df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype={"google_cid": str})
    df = df[df["korean_name"].notna()].copy()  # 가게 정보 없는 영상 행 제외

    is_michelin = df["is_michelin"].astype(str).str.lower() == "true"
    match_failed = df["note"].fillna("").str.contains("사람이 확인 필요")
    is_lodging = df["note"].fillna("").str.contains("숙소로 매칭됨")

    reasons = pd.Series("", index=df.index)
    reasons[is_michelin & (df["latest_grade"] == "UNKNOWN")] += "미슐랭 등재·등급 미확인; "
    reasons[is_michelin & (df["latest_grade"] != "UNKNOWN")] += "미슐랭 등재(등급 확인 필요); "
    reasons[match_failed] += "Google 매칭 실패; "
    reasons[is_lodging] += "숙소로 매칭됨; "

    out = df[reasons != ""].copy()
    out.insert(0, "review_reason", reasons[reasons != ""])
    out = out[REVIEW_COLUMNS]
    # 사람이 채울 열
    out["확인_매칭맞음(O/X)"] = ""
    out["확인_실제등급"] = ""
    out["확인_메모"] = ""
    out.to_excel(out_path, index=False)
    print(f"확인 대상 {len(out)}곳 / 전체 {len(df)}곳 -> {out_path}")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name(src.stem + "_review.xlsx")
    main(src, dst)
