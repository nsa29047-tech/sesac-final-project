package com.sesac.restaurant.favorite;

import com.jayway.jsonpath.JsonPath;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

import static org.junit.jupiter.api.Assumptions.assumeTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** 즐겨찾기 API(APP_DB_URL 이 있을 때만 실행). 식당 행이 필요한 테스트는 public.restaurants 가 비어 있으면 건너뛴다. */
@SpringBootTest(properties = "app.jwt.secret=test-only-secret-test-only-secret-0123")
@AutoConfigureMockMvc
@Transactional
@EnabledIfEnvironmentVariable(named = "APP_DB_URL", matches = ".+")
class FavoriteApiTest {

    private static final String PASSWORD = "password-1234";

    @Autowired MockMvc mvc;
    @Autowired EntityManager em;

    /** 가입하고 로그인해서 Authorization 헤더 값을 돌려준다. */
    private String newUserToken() throws Exception {
        String email = "f-" + UUID.randomUUID() + "@example.com";
        mvc.perform(post("/api/auth/signup").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"%s","password":"%s","nickname":"tester"}""".formatted(email, PASSWORD)))
                .andExpect(status().isCreated());
        String body = mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"%s","password":"%s"}""".formatted(email, PASSWORD)))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return "Bearer " + JsonPath.<String>read(body, "$.accessToken");
    }

    private long anyRestaurantId() {
        Object id = em.createNativeQuery("SELECT min(restaurant_id) FROM public.restaurants").getSingleResult();
        assumeTrue(id != null, "public.restaurants 가 비어 있어 건너뜀");
        return ((Number) id).longValue();
    }

    @Test
    void addIsIdempotentAndListShowsRestaurant() throws Exception {
        long rid = anyRestaurantId();
        String auth = newUserToken();

        mvc.perform(put("/api/favorites/" + rid).header("Authorization", auth)).andExpect(status().isNoContent());
        mvc.perform(put("/api/favorites/" + rid).header("Authorization", auth)).andExpect(status().isNoContent());

        mvc.perform(get("/api/favorites").header("Authorization", auth))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].restaurantId").value(rid))
                .andExpect(jsonPath("$[0].name").isNotEmpty())
                .andExpect(jsonPath("$[0].favoritedAt").isNotEmpty());
    }

    @Test
    void removeIsIdempotent() throws Exception {
        long rid = anyRestaurantId();
        String auth = newUserToken();
        mvc.perform(put("/api/favorites/" + rid).header("Authorization", auth)).andExpect(status().isNoContent());

        mvc.perform(delete("/api/favorites/" + rid).header("Authorization", auth)).andExpect(status().isNoContent());
        mvc.perform(delete("/api/favorites/" + rid).header("Authorization", auth)).andExpect(status().isNoContent());

        mvc.perform(get("/api/favorites").header("Authorization", auth))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void favoritesAreSeparatePerUser() throws Exception {
        long rid = anyRestaurantId();
        String owner = newUserToken();
        String other = newUserToken();
        mvc.perform(put("/api/favorites/" + rid).header("Authorization", owner)).andExpect(status().isNoContent());

        mvc.perform(get("/api/favorites").header("Authorization", other))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(0));
        // 남의 즐겨찾기는 지울 수 없다(내 목록에서만 지워진다)
        mvc.perform(delete("/api/favorites/" + rid).header("Authorization", other)).andExpect(status().isNoContent());
        mvc.perform(get("/api/favorites").header("Authorization", owner))
                .andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void unknownRestaurantIs404() throws Exception {
        String auth = newUserToken();
        mvc.perform(put("/api/favorites/999999999999").header("Authorization", auth))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("RESTAURANT_NOT_FOUND"));
    }

    @Test
    void requiresToken() throws Exception {
        mvc.perform(get("/api/favorites")).andExpect(status().isUnauthorized());
        mvc.perform(put("/api/favorites/1")).andExpect(status().isUnauthorized());
        mvc.perform(delete("/api/favorites/1")).andExpect(status().isUnauthorized());
    }
}
