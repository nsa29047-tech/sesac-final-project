package com.sesac.restaurant.domain;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.io.Serializable;
import java.time.Instant;
import java.util.Objects;

/**
 * users N:M restaurants 의 연결 테이블. restaurants 는 파이프라인이 관리하는 테이블이라 엔티티로 매핑하지 않고
 * restaurant_id 값만 가진다(FK 는 DB 가 보장한다).
 */
@Entity
@Table(name = "favorites", schema = "app")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class Favorite {

    @EmbeddedId
    private FavoriteId id;

    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt = Instant.now();

    public Favorite(Long userId, Long restaurantId) {
        this.id = new FavoriteId(userId, restaurantId);
    }

    @Embeddable
    @Getter
    @NoArgsConstructor(access = AccessLevel.PROTECTED)
    public static class FavoriteId implements Serializable {

        @Column(name = "user_id")
        private Long userId;

        @Column(name = "restaurant_id")
        private Long restaurantId;

        public FavoriteId(Long userId, Long restaurantId) {
            this.userId = userId;
            this.restaurantId = restaurantId;
        }

        @Override
        public boolean equals(Object o) {
            if (this == o) return true;
            if (!(o instanceof FavoriteId other)) return false;
            return Objects.equals(userId, other.userId) && Objects.equals(restaurantId, other.restaurantId);
        }

        @Override
        public int hashCode() {
            return Objects.hash(userId, restaurantId);
        }
    }
}
