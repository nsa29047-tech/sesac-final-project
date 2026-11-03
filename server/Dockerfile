# syntax=docker/dockerfile:1
# Spring Boot 서버. 빌드 컨텍스트는 server/ 폴더다.
#   docker build -t sesac-server server/

# ---- 1단계: 빌드 -------------------------------------------------------------------------
FROM eclipse-temurin:17-jdk AS build
WORKDIR /workspace
# 빌드 설정만 먼저 복사해 의존성을 받아 둔다(소스만 바뀌면 이 레이어는 캐시된다)
COPY gradlew settings.gradle build.gradle ./
COPY gradle gradle
RUN --mount=type=cache,target=/root/.gradle chmod +x gradlew && ./gradlew --no-daemon dependencies > /dev/null
COPY src src
# 테스트는 DB 가 필요해서 이미지 빌드에서는 돌리지 않는다(CI 에서 따로 돌린다)
RUN --mount=type=cache,target=/root/.gradle ./gradlew --no-daemon bootJar -x test \
 && java -Djarmode=tools -jar build/libs/restaurant-server-*-SNAPSHOT.jar extract --destination /workspace/extracted

# ---- 2단계: 실행 이미지 ---------------------------------------------------------------------
FROM eclipse-temurin:17-jre-alpine
RUN addgroup -S app && adduser -S app -G app
WORKDIR /app
# 라이브러리(자주 안 바뀜)와 애플리케이션 jar(자주 바뀜)를 다른 레이어로 둔다
COPY --from=build /workspace/extracted/lib ./lib
COPY --from=build /workspace/extracted/restaurant-server-*-SNAPSHOT.jar ./app.jar
USER app
EXPOSE 8081
# 컨테이너가 메모리 제한을 받으면 그 안에서 힙을 잡는다
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75"
# /actuator/health 는 DB 연결까지 확인한다
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD wget -qO- http://127.0.0.1:8081/actuator/health | grep -q '"status":"UP"' || exit 1
ENTRYPOINT ["java", "-jar", "app.jar"]
