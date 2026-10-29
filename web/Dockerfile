# syntax=docker/dockerfile:1
# Next.js 화면. 빌드 컨텍스트는 web/ 폴더다.
#   docker build --build-arg NEXT_PUBLIC_API_URL=http://localhost:8081 -t sesac-web web/

# ---- 1단계: 의존성 -----------------------------------------------------------------------
FROM node:24-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# ---- 2단계: 빌드 -------------------------------------------------------------------------
FROM node:24-slim AS build
WORKDIR /app
# 브라우저가 접속할 Spring 서버 주소. NEXT_PUBLIC_ 값은 빌드할 때 코드에 박히므로 바꾸려면 다시 빌드한다
ARG NEXT_PUBLIC_API_URL=http://localhost:8081
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_OUTPUT=standalone \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ---- 3단계: 실행 이미지 ---------------------------------------------------------------------
FROM node:24-slim AS run
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
# standalone 은 실행에 필요한 파일만 담는다. 정적 파일과 public 은 직접 복사해야 server.js 가 서빙한다
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"
CMD ["node", "server.js"]
