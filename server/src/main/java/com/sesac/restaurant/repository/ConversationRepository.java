package com.sesac.restaurant.repository;

import com.sesac.restaurant.domain.Conversation;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface ConversationRepository extends JpaRepository<Conversation, UUID> {

    /** 내 대화 목록(최근 순). idx_conversations_user_updated 를 탄다. */
    List<Conversation> findByUserIdOrderByUpdatedAtDesc(Long userId);

    /** 남의 대화는 없는 것처럼 보이게 항상 사용자 조건과 함께 조회한다. */
    Optional<Conversation> findByIdAndUserId(UUID id, Long userId);

    /** 메시지는 DB 의 ON DELETE CASCADE 로 함께 지워진다. */
    @Modifying
    @Query("DELETE FROM Conversation c WHERE c.id = :id AND c.user.id = :userId")
    int deleteByIdAndUserId(@Param("id") UUID id, @Param("userId") Long userId);
}
