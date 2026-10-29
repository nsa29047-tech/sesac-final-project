# syntax=docker/dockerfile:1
# 챗봇 서버(FastAPI, src/api/chat_server.py). 빌드 컨텍스트는 저장소 루트다(.dockerignore 가 허용 목록).
#   docker build -f docker/chatbot.Dockerfile -t sesac-chatbot .

# ---- 1단계: 의존성 설치 --------------------------------------------------------------------
FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
# 의존성 정의만 먼저 복사해 소스가 바뀌어도 이 레이어는 캐시된다
COPY pyproject.toml uv.lock docker/chatbot.deps.txt ./
# uv.lock 의 고정 버전을 constraints 로 쓰고, 설치는 chatbot.deps.txt 의 패키지만 한다
RUN uv export --frozen --no-dev --no-hashes --no-emit-project --no-emit-package parse-apis -o /tmp/constraints.txt \
 && uv venv /opt/venv \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache -r chatbot.deps.txt -c /tmp/constraints.txt

# ---- 2단계: 실행 이미지 --------------------------------------------------------------------
FROM python:3.14-slim
RUN useradd --system --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY src ./src
# 지명 좌표 캐시 같은 실행 중 파일이 쓰이는 곳(compose 에서 볼륨으로 유지한다)
RUN mkdir -p /app/data && chown app:app /app/data
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8000
# DB 까지 닿아야 healthy (/api/health 는 {"ok": bool} 을 돌려준다)
HEALTHCHECK --interval=15s --timeout=6s --start-period=30s --retries=5 \
  CMD python -c "import json,sys,urllib.request; sys.exit(0 if json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)).get('ok') else 1)"
CMD ["python", "src/api/chat_server.py", "--host", "0.0.0.0", "--port", "8000"]
