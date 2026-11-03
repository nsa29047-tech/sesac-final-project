# Docker 사용법 (처음 쓰는 사람용)

이 프로젝트의 전체 스택(`db`, `chatbot`, `server`, `web`)을 Docker Compose로 띄우는 방법을 정리한 문서입니다. 스택 구성과 설계 결정은 [DEVELOPMENT.md](DEVELOPMENT.md)를 봅니다.

## 1. 개념 세 가지

| 용어 | 뜻 | 비유 |
|---|---|---|
| 이미지(image) | 프로그램 실행에 필요한 파일·라이브러리·설정을 묶은 설계도 | 레시피와 재료 세트 |
| 컨테이너(container) | 이미지를 실제로 실행한 것. 지우면 안의 내용도 사라진다 | 완성된 음식 |
| 볼륨(volume) | 컨테이너 밖에 두는 저장 공간. DB 데이터가 여기에 있다 | 냉장고 |

프로그램이 4개라서 컨테이너도 4개이고, `compose.yaml`에 적힌 대로 Docker Compose(`docker compose ...`)가 한꺼번에 띄운다.

## 2. 처음 한 번 준비

1. Docker Desktop을 실행해 둔다(꺼져 있으면 `Cannot connect to the Docker daemon` 오류).
2. 환경 파일을 만들고 값을 채운다(git 제외 파일, 커밋 금지).

```bash
cp docker/stack.env.example docker/stack.env
```

채워야 하는 값: `POSTGRES_PASSWORD`, `READONLY_DB_PASSWORD`, `APP_DB_PASSWORD`(이 스택 안에서만 쓰는 새 값, 영문·숫자·`_-` 12자 이상), `JWT_SECRET`, `OPENAI_API_KEY`, `GOOGLE_PLACES_API_KEY`.

3. DB 스냅샷(`data/db_snapshot/public.sql`)이 있어야 한다. 없으면 `uv run python src/db/export_snapshot.py`로 만든다.

## 3. 명령 구조

모든 명령은 아래 접두어로 시작한다. **`--env-file`을 빼면 `stack.env`를 읽지 않아 "JWT_SECRET이 비었다"는 오류가 난다.**

```
docker compose --env-file docker/stack.env <동작> [옵션] [서비스이름]
```

- `up`: 컨테이너를 만들고 시작한다(이미 떠 있으면 그대로 둔다).
- `-d`: 백그라운드로 실행한다(없으면 터미널이 로그로 채워지고, 창을 닫으면 꺼진다).
- `--build`: 이미지를 새로 만들고 시작한다. 처음, 그리고 코드를 고쳤을 때만 필요하다.
- 서비스 이름: `db`, `chatbot`, `server`, `web`(`compose.yaml`의 이름이고, `ps`에 보이는 `chatbot-1`은 컨테이너 이름이다).

## 4. 자주 쓰는 명령

접두어(`docker compose --env-file docker/stack.env`)는 생략하고 뒷부분만 적는다.

| 하고 싶은 일 | 명령 |
|---|---|
| 처음 실행 / 코드 수정 후 실행 | `up -d --build` |
| 이미 만든 것을 다시 켜기 | `up -d` |
| 상태 보기 | `ps` |
| 최근 로그 보기 | `logs chatbot --tail 50` |
| 로그 실시간으로 보기 | `logs -f chatbot` (`Ctrl+C`로 빠져나와도 컨테이너는 계속 돈다) |
| 전부 끄기(데이터 유지) | `down` |
| 데이터까지 초기화 | `down -v` (**DB가 사라진다**) |
| 한 서비스만 새로 만들기 | `up -d --force-recreate chatbot` |

완성된 형태의 예:

```bash
docker compose --env-file docker/stack.env ps
```

## 5. 무엇을 바꿨느냐에 따라 필요한 명령

| 바꾼 것 | 필요한 명령 |
|---|---|
| `stack.env`의 키·비밀번호 | `up -d --force-recreate <서비스>` |
| Python / Java / 웹 코드 | `up -d --build` |
| 아무것도 안 바꾸고 켜기 | `up -d` |

`restart`와 `--force-recreate`는 다르다. `docker compose restart`는 같은 컨테이너를 껐다 켤 뿐이라 `stack.env`를 다시 읽지 않는다. 키나 비밀번호를 바꿨다면 `--force-recreate`로 컨테이너를 새로 만들어야 반영된다(`--build`는 필요 없다. 키는 이미지가 아니라 실행 시 환경변수로 들어간다).

챗봇 컨테이너를 다시 만들면 챗봇의 대화 기억(서버 메모리)이 사라진다. Spring이 저장한 대화 기록은 남지만 이어서 질문하면 이전 맥락을 기억하지 못한다.

## 6. 하루 사용 흐름

```bash
# 시작 (코드를 안 바꿨다면 --build 생략)
docker compose --env-file docker/stack.env up -d

# 4개가 모두 healthy 인지 확인 (첫 기동은 DB 복원 때문에 몇 분 걸린다)
docker compose --env-file docker/stack.env ps

# 브라우저: http://localhost:3000 -> 가입 -> 질문

# 이상하면 로그 확인
docker compose --env-file docker/stack.env logs chatbot --tail 50

# 끝날 때
docker compose --env-file docker/stack.env down
```

PowerShell에서 접두어를 줄이고 싶으면(해당 터미널 창에서만 유효):

```powershell
function dc { docker compose --env-file docker/stack.env @args }
# 이후: dc up -d, dc ps, dc logs chatbot
```

## 7. 문제 해결

| 증상 | 원인 / 해결 |
|---|---|
| `required variable JWT_SECRET is missing a value` | `--env-file docker/stack.env`를 빼먹었거나 `stack.env`의 해당 값이 비어 있다 |
| `READONLY_DB_PASSWORD variable is not set` 경고 | 위와 같다(`--env-file` 누락) |
| 챗봇 로그에 `OpenAIAuthenticationError`, `401 ... API key has expired` | `stack.env`의 `OPENAI_API_KEY`가 만료됐다. 새 키로 바꾸고 `up -d --force-recreate chatbot`. `.env`의 키와 `stack.env`의 키는 별개 파일이라 한쪽만 바꾸면 어긋난다 |
| `Cannot connect to the Docker daemon` | Docker Desktop이 꺼져 있다 |
| `port is already allocated` | 3000(웹)·8081(Spring)·5434(DB) 포트를 다른 프로그램이 쓴다. `npm run dev` 개발 서버가 흔한 원인 |
| 컨테이너가 `unhealthy` 또는 `Exit` | `logs <서비스>`로 마지막 오류를 읽는다 |
| 코드를 고쳤는데 반영 안 됨 | `--build` 없이 `up`만 했다. 코드는 이미지 안에 복사되어 있다 |

## 8. 접속 주소

| 서비스 | 주소 | 비고 |
|---|---|---|
| web | http://localhost:3000 | 브라우저에서 사용 |
| server (Spring) | http://127.0.0.1:8081 | 웹이 호출하는 API, `/actuator/health`로 상태 확인 |
| db | 127.0.0.1:5434 | 스냅샷 복사본(Supabase 변경이 자동 반영되지 않음) |
| chatbot | 외부 포트 없음 | Spring만 접근할 수 있다 |

## 9. 참고

- DB 초기화(`docker/db/init/10-init.sh`)는 볼륨이 비어 있을 때 **처음 한 번만** 실행된다. `down`/`up`으로는 다시 실행되지 않고, `down -v`로 볼륨을 지워야 다시 복원된다.
- Docker 없이 챗봇만 써 보려면 `uv run python src/chatbot/cli.py --trace "질문"` 또는 `uv run streamlit run src/streamlit_app.py`.
