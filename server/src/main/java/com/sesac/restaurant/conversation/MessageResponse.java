package com.sesac.restaurant.conversation;

import com.sesac.restaurant.domain.Message;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

import java.time.Instant;
import java.util.Arrays;
import java.util.List;

/**
 * @param restaurants 챗봇이 보낸 restaurants 이벤트({restaurants: [...], map_extra: [...]}) 그대로. 카드가 없는 메시지는 null
 */
public record MessageResponse(Long id, String role, String content, List<Long> restaurantIds, JsonNode restaurants,
                              Instant createdAt) {

    static MessageResponse from(Message m, JsonMapper mapper) {
        List<Long> ids = m.getRestaurantIds() == null ? List.of() : Arrays.asList(m.getRestaurantIds());
        return new MessageResponse(m.getId(), m.getRole().name(), m.getContent(), ids, parse(m.getRestaurantsJson(), mapper),
                m.getCreatedAt());
    }

    /** 저장된 JSON 이 깨져 있어도 대화 조회 전체가 실패하지 않게 카드만 비운다. */
    private static JsonNode parse(String json, JsonMapper mapper) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return mapper.readTree(json);
        } catch (RuntimeException e) {
            return null;
        }
    }
}
