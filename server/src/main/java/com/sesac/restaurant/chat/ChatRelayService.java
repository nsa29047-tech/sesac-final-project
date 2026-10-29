package com.sesac.restaurant.chat;

import com.sesac.restaurant.common.ApiException;
import com.sesac.restaurant.conversation.ConversationService;
import com.sesac.restaurant.domain.Message;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 브라우저의 질문을 FastAPI 챗봇에 전달하고, SSE 이벤트를 오는 대로 브라우저에 흘려 보내면서 대화를 DB 에 저장한다.
 *
 * <pre>
 * 1. 동시 스트림 수 확인(세마포어) -> 남의 대화 id 인지 확인
 * 2. FastAPI 호출(응답 헤더까지). 409·다운이면 여기서 끝나고 아무것도 저장하지 않는다
 * 3. 사용자 메시지 저장(대화가 없으면 함께 생성)
 * 4. 별도 스레드가 이벤트를 읽어 그대로 전달. done 이 오면 assistant 메시지를 저장한 뒤 전달
 * </pre>
 * 스트리밍 중에는 DB 트랜잭션을 잡고 있지 않는다(저장은 각각 짧은 트랜잭션).
 * 답변이 끝나지 못하고(오류·끊김) 끝나면 사용자 메시지만 남고 assistant 메시지는 저장하지 않는다.
 */
@Service
public class ChatRelayService {

    private static final Logger log = LoggerFactory.getLogger(ChatRelayService.class);
    /** FastAPI 의 전체 응답 제한(60초)보다 조금 길게 잡는다. */
    private static final long EMITTER_TIMEOUT_MS = 90_000;

    private final ConversationService conversations;
    private final ChatUpstream upstream;
    private final JsonMapper mapper;
    private final Semaphore permits;
    private final ExecutorService executor;

    public ChatRelayService(ConversationService conversations, ChatUpstream upstream, JsonMapper mapper,
                            ChatProperties props) {
        this.conversations = conversations;
        this.upstream = upstream;
        this.mapper = mapper;
        this.permits = new Semaphore(props.maxConcurrent());
        AtomicInteger n = new AtomicInteger();
        // 동시 개수는 세마포어가 제한하므로 스레드 풀은 따로 상한을 두지 않는다
        this.executor = Executors.newCachedThreadPool(r -> new Thread(r, "chat-relay-" + n.incrementAndGet()));
    }

    @PreDestroy
    void shutdown() {
        executor.shutdownNow();
    }

    public SseEmitter relay(Long userId, UUID requestedId, String message) {
        if (!permits.tryAcquire()) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "CHAT_OVERLOADED", "요청이 많아 잠시 후 다시 시도해 주세요");
        }
        boolean handedOff = false;
        InputStream stream = null;
        try {
            UUID id = requestedId != null ? requestedId : UUID.randomUUID();
            conversations.assertUsable(userId, id);
            stream = upstream.open(id, message);
            conversations.startTurn(userId, id, message);

            SseEmitter emitter = new SseEmitter(EMITTER_TIMEOUT_MS);
            InputStream in = stream;
            AtomicBoolean finished = new AtomicBoolean();
            // 브라우저가 끊거나 시간이 지나면 FastAPI 연결을 닫아 그래프 실행도 멈추게 한다
            emitter.onCompletion(() -> {
                finished.set(true);
                closeQuietly(in);
            });
            emitter.onError(e -> {
                finished.set(true);
                closeQuietly(in);
            });
            emitter.onTimeout(emitter::complete);
            executor.execute(() -> pump(userId, id, in, emitter, finished));
            handedOff = true;
            return emitter;
        } catch (RuntimeException e) {
            closeQuietly(stream);
            throw e;
        } finally {
            if (!handedOff) {
                permits.release();
            }
        }
    }

    /** 스트림을 읽는 스레드 하나만 만지는 한 턴의 상태. */
    private static final class Turn {
        boolean terminal;                         // done 또는 error 를 이미 받았다
        List<Long> restaurantIds = List.of();     // restaurants 이벤트의 카드 식당 id
    }

    /** 브라우저 쪽 전송이 실패했다는 표시(업스트림 읽기 오류와 구분한다). */
    private static final class ClientGone extends RuntimeException {
        ClientGone(Throwable cause) {
            super(cause);
        }
    }

    private void pump(Long userId, UUID id, InputStream in, SseEmitter emitter, AtomicBoolean finished) {
        Turn turn = new Turn();
        try (in) {
            SseFrameReader.read(in, frame -> handle(userId, id, frame, turn, emitter));
            if (!turn.terminal) {
                sendError(emitter, "upstream_closed", "챗봇 응답이 중간에 끊겼습니다");
            }
            emitter.complete();
        } catch (ClientGone e) {
            log.debug("client disconnected: {}", id);
            completeQuietly(emitter);
        } catch (IOException | RuntimeException e) {
            if (finished.get()) {
                log.debug("relay stopped after completion: {}", id);   // 브라우저가 끊어서 우리가 연결을 닫은 경우
            } else {
                log.warn("chat relay failed: {}", id, e);
                try {
                    sendError(emitter, "upstream_error", "챗봇 응답을 받는 중 오류가 났습니다");
                } catch (ClientGone ignored) {
                    // 이미 브라우저가 없다
                }
            }
            completeQuietly(emitter);
        } finally {
            permits.release();
        }
    }

    private void handle(Long userId, UUID id, SseFrame frame, Turn turn, SseEmitter emitter) {
        if (turn.terminal) {
            return;
        }
        switch (frame.event()) {
            case "restaurants" -> turn.restaurantIds = cardIds(frame.data());
            case "error" -> turn.terminal = true;
            case "done" -> {
                turn.terminal = true;
                // 클라이언트가 done 을 받았을 때 바로 대화를 조회해도 답변이 보이도록 저장을 먼저 한다
                saveAnswer(userId, id, frame.data(), turn.restaurantIds);
            }
            default -> {
            }
        }
        send(emitter, frame.event(), frame.data());
    }

    private void saveAnswer(Long userId, UUID id, String doneData, List<Long> restaurantIds) {
        try {
            String answer = mapper.readTree(doneData).path("answer").asString("");
            if (!answer.isBlank()) {
                conversations.append(userId, id, Message.Role.assistant, answer, restaurantIds);
            }
        } catch (RuntimeException e) {
            // 답변은 이미 화면에 흘려 보냈으니 저장 실패만 기록하고 done 은 그대로 전달한다
            log.error("failed to save assistant message: {}", id, e);
        }
    }

    private List<Long> cardIds(String data) {
        try {
            List<Long> ids = new ArrayList<>();
            for (JsonNode card : mapper.readTree(data).path("restaurants")) {
                long restaurantId = card.path("restaurant_id").asLong(0);
                if (restaurantId > 0) {
                    ids.add(restaurantId);
                }
            }
            return ids;
        } catch (RuntimeException e) {
            log.warn("failed to parse restaurants event", e);
            return List.of();
        }
    }

    private void sendError(SseEmitter emitter, String code, String message) {
        send(emitter, "error", mapper.writeValueAsString(Map.of("code", code, "message", message)));
    }

    private static void send(SseEmitter emitter, String event, String data) {
        try {
            emitter.send(SseEmitter.event().name(event).data(data));
        } catch (IOException | IllegalStateException e) {
            throw new ClientGone(e);
        }
    }

    private static void completeQuietly(SseEmitter emitter) {
        try {
            emitter.complete();
        } catch (RuntimeException ignored) {
            // 이미 끝났거나 끊겼다
        }
    }

    private static void closeQuietly(InputStream in) {
        if (in == null) {
            return;
        }
        try {
            in.close();
        } catch (IOException ignored) {
            // 취소 목적의 닫기라 실패해도 할 일이 없다
        }
    }
}
