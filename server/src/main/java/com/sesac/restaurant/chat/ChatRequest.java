package com.sesac.restaurant.chat;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

import java.util.UUID;

/** conversationId 는 프론트가 첫 질문 때 만든 UUID. 없으면 서버가 새 대화를 만든다. 질문 길이 제한은 챗봇 서버와 같다(500자). */
public record ChatRequest(UUID conversationId, @NotBlank @Size(max = 500) String message) {
}
