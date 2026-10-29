package com.sesac.restaurant.conversation;

import jakarta.validation.constraints.Size;

import java.util.UUID;

/** id 는 프론트가 첫 질문 때 만든 챗봇 thread_id 를 그대로 받을 때만 넣는다(없으면 서버가 만든다). */
public record CreateConversationRequest(UUID id, @Size(max = 200) String title) {
}
