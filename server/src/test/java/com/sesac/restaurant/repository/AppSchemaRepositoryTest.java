package com.sesac.restaurant.repository;

import com.sesac.restaurant.domain.Conversation;
import com.sesac.restaurant.domain.Favorite;
import com.sesac.restaurant.domain.Message;
import com.sesac.restaurant.domain.User;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.data.jpa.test.autoconfigure.DataJpaTest;
import org.springframework.boot.jdbc.test.autoconfigure.AutoConfigureTestDatabase;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * migrate_app_schema.sql 을 적용한 실제 Postgres 에 붙어 매핑을 검증한다(APP_DB_URL 이 있을 때만 실행).
 * 각 테스트는 트랜잭션 안에서 돌고 끝나면 롤백된다.
 */
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@EnabledIfEnvironmentVariable(named = "APP_DB_URL", matches = ".+")
class AppSchemaRepositoryTest {

    @Autowired UserRepository users;
    @Autowired ConversationRepository conversations;
    @Autowired MessageRepository messages;
    @Autowired FavoriteRepository favorites;
    @Autowired EntityManager em;

    @Test
    void conversationsAreListedNewestFirstAndMessagesInOrder() {
        User user = users.save(new User("test-" + UUID.randomUUID() + "@example.com", "hash", "tester"));
        Conversation older = conversations.save(new Conversation(UUID.randomUUID(), user, "older"));
        Conversation newer = conversations.save(new Conversation(UUID.randomUUID(), user, "newer"));
        newer.touch();
        em.flush();

        assertThat(conversations.findByUserIdOrderByUpdatedAtDesc(user.getId()))
                .extracting(Conversation::getTitle).containsExactly("newer", "older");

        messages.save(new Message(older, Message.Role.user, "강남역 일식집", null));
        messages.save(new Message(older, Message.Role.assistant, "추천합니다", new Long[]{1L, 2L, 3L}));
        em.flush();
        em.clear();

        List<Message> saved = messages.findByConversationIdOrderByCreatedAtAscIdAsc(older.getId());
        assertThat(saved).extracting(Message::getRole)
                .containsExactly(Message.Role.user, Message.Role.assistant);
        assertThat(saved.get(0).getRestaurantIds()).isNull();
        assertThat(saved.get(1).getRestaurantIds()).containsExactly(1L, 2L, 3L);
    }

    @Test
    void favoriteUsesCompositeKeyAndSavingTwiceDoesNotDuplicate() {
        Object restaurantId = em.createNativeQuery("SELECT min(restaurant_id) FROM public.restaurants").getSingleResult();
        assumeTrue(restaurantId != null, "public.restaurants 가 비어 있어 즐겨찾기 테스트를 건너뜀");

        User user = users.save(new User("fav-" + UUID.randomUUID() + "@example.com", "hash", "tester"));
        long rid = ((Number) restaurantId).longValue();
        favorites.save(new Favorite(user.getId(), rid));
        favorites.save(new Favorite(user.getId(), rid));
        em.flush();

        assertThat(favorites.findByIdUserIdOrderByCreatedAtDesc(user.getId())).hasSize(1);
    }
}
