package com.sesac.restaurant.repository;

import com.sesac.restaurant.domain.Message;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.UUID;

public interface MessageRepository extends JpaRepository<Message, Long> {

    /** 대화 하나의 메시지를 시간순으로(같은 시각이면 저장 순서). idx_messages_conversation_created 를 탄다. */
    List<Message> findByConversationIdOrderByCreatedAtAscIdAsc(UUID conversationId);
}
