package com.sesac.restaurant.chat;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param baseUrl       FastAPI 챗봇 서버 주소(src/api/chat_server.py)
 * @param maxConcurrent 동시에 중계할 수 있는 스트림 수. 넘으면 503 (스트림 하나가 스레드 하나를 쓴다)
 */
@ConfigurationProperties("app.chat")
public record ChatProperties(String baseUrl, int maxConcurrent) {
}
