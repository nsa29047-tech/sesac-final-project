package com.sesac.restaurant.chat;

/** SSE 한 건. data 는 FastAPI 가 보낸 JSON 문자열 그대로다(여러 줄이면 \n 으로 이어 붙인다). */
public record SseFrame(String event, String data) {
}
