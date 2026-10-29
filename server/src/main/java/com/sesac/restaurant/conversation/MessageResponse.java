package com.sesac.restaurant.conversation;

import com.sesac.restaurant.domain.Message;

import java.time.Instant;
import java.util.Arrays;
import java.util.List;

public record MessageResponse(Long id, String role, String content, List<Long> restaurantIds, Instant createdAt) {

    static MessageResponse from(Message m) {
        List<Long> ids = m.getRestaurantIds() == null ? List.of() : Arrays.asList(m.getRestaurantIds());
        return new MessageResponse(m.getId(), m.getRole().name(), m.getContent(), ids, m.getCreatedAt());
    }
}
