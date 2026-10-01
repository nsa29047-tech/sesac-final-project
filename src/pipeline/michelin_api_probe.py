"""Parse의 guide.michelin.com API 호출 확인용 스크립트. 키는 환경변수 MICHELIN_GUIDE_API_KEY 에서 읽는다."""
import os
import sys

from dotenv import load_dotenv

load_dotenv()
# Parse SDK 는 PARSE_API_KEY 를 읽으므로 프로젝트 변수명에서 옮겨 준다.
if "MICHELIN_GUIDE_API_KEY" in os.environ:
    os.environ.setdefault("PARSE_API_KEY", os.environ["MICHELIN_GUIDE_API_KEY"])

from parse_apis.guide_michelin_com_api import MichelinGuide  # noqa: E402


def main(query: str = "Mingles") -> None:
    client = MichelinGuide()
    for r in client.restaurants.search(query=query, limit=3):
        d = r.distinction.slug if r.distinction else "no distinction"
        print(r.name, "|", r.city.name, "|", d, "|", r.slug)


if __name__ == "__main__":
    main(*sys.argv[1:2])
