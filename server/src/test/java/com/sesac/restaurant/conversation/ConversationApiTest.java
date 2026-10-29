package com.sesac.restaurant.conversation;

import com.jayway.jsonpath.JsonPath;
import com.sesac.restaurant.domain.Message;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** 대화·메시지 API(APP_DB_URL 이 있을 때만 실행, 각 테스트는 롤백된다). */
@SpringBootTest(properties = "app.jwt.secret=test-only-secret-test-only-secret-0123")
@AutoConfigureMockMvc
@Transactional
@EnabledIfEnvironmentVariable(named = "APP_DB_URL", matches = ".+")
class ConversationApiTest {

    private static final String PASSWORD = "password-1234";

    @Autowired MockMvc mvc;
    @Autowired ConversationService service;
    @Autowired EntityManager em;

    private record Account(String auth, long userId) {
    }

    private Account newAccount() throws Exception {
        String email = "c-" + UUID.randomUUID() + "@example.com";
        mvc.perform(post("/api/auth/signup").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"%s","password":"%s","nickname":"tester"}""".formatted(email, PASSWORD)))
                .andExpect(status().isCreated());
        String login = mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"%s","password":"%s"}""".formatted(email, PASSWORD)))
                .andReturn().getResponse().getContentAsString();
        String auth = "Bearer " + JsonPath.<String>read(login, "$.accessToken");
        String me = mvc.perform(get("/api/me").header("Authorization", auth))
                .andReturn().getResponse().getContentAsString();
        return new Account(auth, ((Number) JsonPath.read(me, "$.id")).longValue());
    }

    private void createConversation(Account a, UUID id, String body) throws Exception {
        mvc.perform(post("/api/conversations").header("Authorization", a.auth())
                        .contentType(MediaType.APPLICATION_JSON).content(body.formatted(id)))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").value(id.toString()));
    }

    @Test
    void createWithClientIdAndWithoutBody() throws Exception {
        Account a = newAccount();
        UUID id = UUID.randomUUID();
        createConversation(a, id, """
                {"id":"%s"}""");

        // 본문이 없으면 서버가 id 를 만들고 기본 제목을 쓴다
        mvc.perform(post("/api/conversations").header("Authorization", a.auth()))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").isNotEmpty())
                .andExpect(jsonPath("$.title").value("새 대화"));

        mvc.perform(get("/api/conversations").header("Authorization", a.auth()))
                .andExpect(jsonPath("$.length()").value(2));
    }

    @Test
    void duplicateIdIs409AndDoesNotOverwriteOthersConversation() throws Exception {
        Account owner = newAccount();
        Account other = newAccount();
        UUID id = UUID.randomUUID();
        createConversation(owner, id, """
                {"id":"%s","title":"내 대화"}""");

        mvc.perform(post("/api/conversations").header("Authorization", other.auth())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"id":"%s","title":"가로채기"}""".formatted(id)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("DUPLICATE_CONVERSATION"));

        mvc.perform(get("/api/conversations").header("Authorization", owner.auth()))
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].title").value("내 대화"));
        mvc.perform(get("/api/conversations").header("Authorization", other.auth()))
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void messagesAreOrderedAndFirstUserMessageSetsTitleAndConversationMovesToTop() throws Exception {
        Account a = newAccount();
        UUID older = UUID.randomUUID();
        UUID newer = UUID.randomUUID();
        createConversation(a, older, """
                {"id":"%s"}""");
        createConversation(a, newer, """
                {"id":"%s"}""");

        service.append(a.userId(), older, Message.Role.user, "강남역 근처 일식집 추천해줘", null);
        service.append(a.userId(), older, Message.Role.assistant, "세 곳을 추천합니다", List.of(11L, 22L, 33L));
        service.append(a.userId(), older, Message.Role.user, "두 번째 질문은 제목이 되지 않는다", null);

        mvc.perform(get("/api/conversations/" + older + "/messages").header("Authorization", a.auth()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(3))
                .andExpect(jsonPath("$[0].role").value("user"))
                .andExpect(jsonPath("$[0].restaurantIds.length()").value(0))
                .andExpect(jsonPath("$[1].role").value("assistant"))
                .andExpect(jsonPath("$[1].restaurantIds[0]").value(11))
                .andExpect(jsonPath("$[1].restaurantIds[2]").value(33))
                .andExpect(jsonPath("$[2].content").value("두 번째 질문은 제목이 되지 않는다"));

        // 메시지가 추가된 대화가 목록 맨 위로 오고, 제목은 첫 사용자 메시지로 정해진다
        mvc.perform(get("/api/conversations").header("Authorization", a.auth()))
                .andExpect(jsonPath("$[0].id").value(older.toString()))
                .andExpect(jsonPath("$[0].title").value("강남역 근처 일식집 추천해줘"))
                .andExpect(jsonPath("$[1].title").value("새 대화"));
    }

    @Test
    void longFirstMessageGetsTruncatedTitle() throws Exception {
        Account a = newAccount();
        UUID id = UUID.randomUUID();
        createConversation(a, id, """
                {"id":"%s"}""");
        service.append(a.userId(), id, Message.Role.user, "가".repeat(100), null);

        mvc.perform(get("/api/conversations").header("Authorization", a.auth()))
                .andExpect(jsonPath("$[0].title").value("가".repeat(30) + "…"));
    }

    @Test
    void otherUsersCannotReadRenameOrDelete() throws Exception {
        Account owner = newAccount();
        Account other = newAccount();
        UUID id = UUID.randomUUID();
        createConversation(owner, id, """
                {"id":"%s","title":"비공개"}""");
        service.append(owner.userId(), id, Message.Role.user, "비밀 질문", null);

        mvc.perform(get("/api/conversations/" + id + "/messages").header("Authorization", other.auth()))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("CONVERSATION_NOT_FOUND"));
        mvc.perform(patch("/api/conversations/" + id).header("Authorization", other.auth())
                        .contentType(MediaType.APPLICATION_JSON).content("""
                                {"title":"해킹"}"""))
                .andExpect(status().isNotFound());
        mvc.perform(delete("/api/conversations/" + id).header("Authorization", other.auth()))
                .andExpect(status().isNotFound());

        // 소유자의 대화는 그대로다. 없는 대화와 같은 404 라서 존재 여부도 드러나지 않는다
        mvc.perform(get("/api/conversations/" + id + "/messages").header("Authorization", owner.auth()))
                .andExpect(jsonPath("$.length()").value(1));
        mvc.perform(get("/api/conversations/" + UUID.randomUUID() + "/messages").header("Authorization", owner.auth()))
                .andExpect(status().isNotFound());
    }

    @Test
    void renameAndDeleteRemoveMessagesToo() throws Exception {
        Account a = newAccount();
        UUID id = UUID.randomUUID();
        createConversation(a, id, """
                {"id":"%s"}""");
        service.append(a.userId(), id, Message.Role.user, "질문", null);

        mvc.perform(patch("/api/conversations/" + id).header("Authorization", a.auth())
                        .contentType(MediaType.APPLICATION_JSON).content("""
                                {"title":"바꾼 제목"}"""))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.title").value("바꾼 제목"));

        mvc.perform(delete("/api/conversations/" + id).header("Authorization", a.auth()))
                .andExpect(status().isNoContent());
        mvc.perform(get("/api/conversations/" + id + "/messages").header("Authorization", a.auth()))
                .andExpect(status().isNotFound());

        em.clear();
        Number left = (Number) em.createNativeQuery("SELECT count(*) FROM app.messages WHERE conversation_id = :id")
                .setParameter("id", id).getSingleResult();
        assertThat(left.intValue()).isZero();
    }

    @Test
    void invalidInputIs400AndTokenIsRequired() throws Exception {
        Account a = newAccount();
        mvc.perform(post("/api/conversations").header("Authorization", a.auth())
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"title":"%s"}""".formatted("가".repeat(201))))
                .andExpect(status().isBadRequest());
        UUID id = UUID.randomUUID();
        createConversation(a, id, """
                {"id":"%s"}""");
        mvc.perform(patch("/api/conversations/" + id).header("Authorization", a.auth())
                        .contentType(MediaType.APPLICATION_JSON).content("""
                                {"title":"  "}"""))
                .andExpect(status().isBadRequest());

        mvc.perform(get("/api/conversations")).andExpect(status().isUnauthorized());
        mvc.perform(post("/api/conversations")).andExpect(status().isUnauthorized());
    }
}
