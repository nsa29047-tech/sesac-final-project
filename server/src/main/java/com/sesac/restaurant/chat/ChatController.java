package com.sesac.restaurant.chat;

import jakarta.validation.Valid;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * 챗봇 질문. 응답은 FastAPI 의 SSE 이벤트(thread, status, token, restaurants, done 또는 error)를 그대로 전달한다.
 * produces 를 지정하지 않는다: 지정하면 시작 전 오류(409 등)의 JSON 응답이 내용 협상에 걸린다. SseEmitter 가 직접 content-type 을 정한다.
 */
@RestController
@RequestMapping("/api/chat")
public class ChatController {

    private final ChatRelayService relay;

    public ChatController(ChatRelayService relay) {
        this.relay = relay;
    }

    @PostMapping
    public SseEmitter chat(@AuthenticationPrincipal Jwt jwt, @Valid @RequestBody ChatRequest request) {
        return relay.relay(Long.valueOf(jwt.getSubject()), request.conversationId(), request.message());
    }
}
