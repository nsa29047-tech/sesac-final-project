package com.sesac.restaurant.domain;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.springframework.data.domain.Persistable;

import java.time.Instant;
import java.util.UUID;

/**
 * 챗봇의 LangGraph thread_id 와 같은 UUID 를 id 로 쓴다(프론트가 첫 질문 때 만든 값을 그대로 받는다).
 * id 를 직접 정하는 엔티티라 Persistable 로 "새 엔티티"임을 알려 준다. 그러지 않으면 save() 가 merge 로 동작해서
 * 이미 있는 다른 사람의 대화를 덮어쓸 수 있다.
 */
@Entity
@Table(name = "conversations", schema = "app")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Conversation implements Persistable<UUID> {

    public static final String DEFAULT_TITLE = "새 대화";   // DB 기본값과 같다

    @Id
    @Column(name = "conversation_id")
    private UUID id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Column(nullable = false)
    private String title;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt = Instant.now();

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt = Instant.now();

    @Transient
    private boolean newEntity = true;

    public Conversation(UUID id, User user, String title) {
        this.id = id;
        this.user = user;
        this.title = title;
    }

    @Override
    public boolean isNew() {
        return newEntity;
    }

    @PostPersist
    @PostLoad
    void markNotNew() {
        this.newEntity = false;
    }

    public void rename(String title) {
        this.title = title;
    }

    /** 메시지가 추가될 때 호출해 목록 정렬 기준을 갱신한다. */
    public void touch() {
        this.updatedAt = Instant.now();
    }
}
