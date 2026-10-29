package com.sesac.restaurant.domain;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;

@Entity
@Table(name = "messages", schema = "app")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Message {

    public enum Role { user, assistant }   // DB CHECK 제약의 값과 같다

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    @Column(name = "message_id")
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "conversation_id", nullable = false)
    private Conversation conversation;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    private Role role;

    @Column(nullable = false, columnDefinition = "text")
    private String content;

    /** assistant 답변에 카드로 나온 식당 id 들. 없으면 null. */
    @JdbcTypeCode(SqlTypes.ARRAY)
    @Column(name = "restaurant_ids", columnDefinition = "bigint[]")
    private Long[] restaurantIds;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt = Instant.now();

    public Message(Conversation conversation, Role role, String content, Long[] restaurantIds) {
        this.conversation = conversation;
        this.role = role;
        this.content = content;
        this.restaurantIds = restaurantIds;
    }
}
