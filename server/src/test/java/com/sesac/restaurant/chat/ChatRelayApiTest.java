package com.sesac.restaurant.chat;

import com.jayway.jsonpath.JsonPath;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 가짜 FastAPI(JDK HttpServer)를 띄워 중계 전체 흐름을 확인한다(APP_DB_URL 이 있을 때만 실행).
 * 중계 스레드가 별도 트랜잭션으로 저장하므로 테스트를 롤백할 수 없다: 이 테스트가 만든 사용자(relay- 로 시작하는 이메일)는 끝나고 지운다.
 */
@SpringBootTest(properties = "app.jwt.secret=test-only-secret-test-only-secret-0123")
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named = "APP_DB_URL", matches = ".+")
class ChatRelayApiTest {

    private static final String PASSWORD = "password-1234";

    private enum Mode { HAPPY, BUSY, ERROR_EVENT, CUT }

    private static final HttpServer FAKE = startFake();
    private static volatile Mode mode = Mode.HAPPY;
    private static final AtomicInteger calls = new AtomicInteger();
    private static final AtomicReference<String> lastBody = new AtomicReference<>();

    private static HttpServer startFake() {
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/api/chat", exchange -> {
                calls.incrementAndGet();
                lastBody.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
                if (mode == Mode.BUSY) {
                    byte[] body = "{\"detail\":\"busy\"}".getBytes(StandardCharsets.UTF_8);
                    exchange.sendResponseHeaders(409, body.length);
                    exchange.getResponseBody().write(body);
                    exchange.close();
                    return;
                }
                exchange.getResponseHeaders().add("Content-Type", "text/event-stream");
                exchange.sendResponseHeaders(200, 0);   // 길이를 모르는 chunked 응답
                try (OutputStream out = exchange.getResponseBody()) {
                    emit(out, "thread", "{\"thread_id\": \"x\"}");
                    emit(out, "status", "{\"tool\": \"find_restaurants\", \"args\": {}}");
                    emit(out, "token", "{\"text\": \"강남역 \"}");
                    emit(out, "token", "{\"text\": \"일식집\"}");
                    switch (mode) {
                        case HAPPY -> {
                            emit(out, "restaurants", "{\"restaurants\": [{\"restaurant_id\": 11, \"name\": \"A\"}, "
                                    + "{\"restaurant_id\": 22, \"name\": \"B\"}], \"map_extra\": [{\"restaurant_id\": 33}]}");
                            emit(out, "done", "{\"answer\": \"강남역 일식집 두 곳을 추천합니다\"}");
                        }
                        case ERROR_EVENT -> emit(out, "error", "{\"code\": \"internal\", \"message\": \"오류\"}");
                        default -> {
                            // CUT: done 없이 연결을 닫는다
                        }
                    }
                }
            });
            server.start();
            return server;
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    private static void emit(OutputStream out, String event, String data) throws IOException {
        out.write(("event: " + event + "\ndata: " + data + "\n\n").getBytes(StandardCharsets.UTF_8));
        out.flush();
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("app.chat.base-url", () -> "http://127.0.0.1:" + FAKE.getAddress().getPort());
    }

    @AfterAll
    static void stopFake() {
        FAKE.stop(0);
    }

    @Autowired MockMvc mvc;
    @Autowired JdbcTemplate jdbc;

    @BeforeEach
    void reset() {
        mode = Mode.HAPPY;
        calls.set(0);
    }

    @AfterEach
    void cleanUp() {
        jdbc.update("DELETE FROM app.users WHERE email LIKE 'relay-%@example.com'");   // 대화·메시지는 CASCADE
    }

    private String newUserAuth() throws Exception {
        String email = "relay-" + UUID.randomUUID() + "@example.com";
        mvc.perform(post("/api/auth/signup").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"%s","password":"%s","nickname":"tester"}""".formatted(email, PASSWORD)))
                .andExpect(status().isCreated());
        String login = mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"%s","password":"%s"}""".formatted(email, PASSWORD)))
                .andReturn().getResponse().getContentAsString();
        return "Bearer " + JsonPath.<String>read(login, "$.accessToken");
    }

    /** 질문을 보내고 스트림이 끝날 때까지 기다려 응답 본문을 돌려준다. */
    private String ask(String auth, UUID conversationId, String message) throws Exception {
        MvcResult started = mvc.perform(post("/api/chat").header("Authorization", auth)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"conversationId":"%s","message":"%s"}""".formatted(conversationId, message)))
                .andExpect(request().asyncStarted())
                .andReturn();
        started.getAsyncResult(10_000);
        return started.getResponse().getContentAsString(StandardCharsets.UTF_8);
    }

    private List<String> eventNames(String body) {
        return body.lines().filter(l -> l.startsWith("event:")).map(l -> l.substring(6).trim()).toList();
    }

    @Test
    void relaysEventsInOrderAndSavesBothMessages() throws Exception {
        String auth = newUserAuth();
        UUID id = UUID.randomUUID();

        String body = ask(auth, id, "강남역 일식집 추천해줘");

        assertThat(eventNames(body)).containsExactly("thread", "status", "token", "token", "restaurants", "done");
        assertThat(body).contains("강남역 ", "일식집 두 곳을 추천합니다");   // 한글이 깨지지 않고 그대로 전달된다
        // FastAPI 에는 대화 id 가 thread_id 로, 질문이 그대로 전달된다
        assertThat(JsonPath.<String>read(lastBody.get(), "$.thread_id")).isEqualTo(id.toString());
        assertThat(JsonPath.<String>read(lastBody.get(), "$.message")).isEqualTo("강남역 일식집 추천해줘");

        mvc.perform(get("/api/conversations/" + id + "/messages").header("Authorization", auth))
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[0].role").value("user"))
                .andExpect(jsonPath("$[0].content").value("강남역 일식집 추천해줘"))
                .andExpect(jsonPath("$[1].role").value("assistant"))
                .andExpect(jsonPath("$[1].content").value("강남역 일식집 두 곳을 추천합니다"))
                // 카드로 소개한 식당만 저장한다(지도용 map_extra 는 제외)
                .andExpect(jsonPath("$[1].restaurantIds.length()").value(2))
                .andExpect(jsonPath("$[1].restaurantIds[0]").value(11))
                .andExpect(jsonPath("$[1].restaurantIds[1]").value(22));
        mvc.perform(get("/api/conversations").header("Authorization", auth))
                .andExpect(jsonPath("$[0].title").value("강남역 일식집 추천해줘"));
    }

    @Test
    void secondQuestionContinuesTheSameConversation() throws Exception {
        String auth = newUserAuth();
        UUID id = UUID.randomUUID();
        ask(auth, id, "첫 질문");
        ask(auth, id, "두 번째 질문");

        mvc.perform(get("/api/conversations/" + id + "/messages").header("Authorization", auth))
                .andExpect(jsonPath("$.length()").value(4))
                .andExpect(jsonPath("$[2].content").value("두 번째 질문"));
        mvc.perform(get("/api/conversations").header("Authorization", auth))
                .andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void busyUpstreamIs409AndNothingIsSaved() throws Exception {
        String auth = newUserAuth();
        mode = Mode.BUSY;

        mvc.perform(post("/api/chat").header("Authorization", auth).contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"conversationId":"%s","message":"질문"}""".formatted(UUID.randomUUID())))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("CHAT_BUSY"));
        mvc.perform(get("/api/conversations").header("Authorization", auth))
                .andExpect(jsonPath("$.length()").value(0));
    }

    @Test
    void errorEventIsForwardedAndOnlyTheQuestionIsKept() throws Exception {
        String auth = newUserAuth();
        UUID id = UUID.randomUUID();
        mode = Mode.ERROR_EVENT;

        String body = ask(auth, id, "질문");

        assertThat(eventNames(body)).endsWith("error");
        mvc.perform(get("/api/conversations/" + id + "/messages").header("Authorization", auth))
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].role").value("user"));
    }

    @Test
    void connectionCutWithoutDoneBecomesAnErrorEvent() throws Exception {
        String auth = newUserAuth();
        UUID id = UUID.randomUUID();
        mode = Mode.CUT;

        String body = ask(auth, id, "질문");

        assertThat(eventNames(body)).endsWith("error");
        assertThat(body).contains("upstream_closed");
        mvc.perform(get("/api/conversations/" + id + "/messages").header("Authorization", auth))
                .andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void otherUsersConversationIdIsRejectedBeforeCallingUpstream() throws Exception {
        String owner = newUserAuth();
        String other = newUserAuth();
        UUID id = UUID.randomUUID();
        ask(owner, id, "내 질문");
        calls.set(0);

        mvc.perform(post("/api/chat").header("Authorization", other).contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"conversationId":"%s","message":"가로채기"}""".formatted(id)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("DUPLICATE_CONVERSATION"));
        assertThat(calls.get()).isZero();
    }

    @Test
    void withoutConversationIdAServerGeneratedConversationIsCreated() throws Exception {
        String auth = newUserAuth();
        MvcResult started = mvc.perform(post("/api/chat").header("Authorization", auth)
                        .contentType(MediaType.APPLICATION_JSON).content("""
                                {"message":"질문"}"""))
                .andExpect(request().asyncStarted()).andReturn();
        started.getAsyncResult(10_000);

        mvc.perform(get("/api/conversations").header("Authorization", auth))
                .andExpect(jsonPath("$.length()").value(1));
    }

    @Test
    void invalidRequestsAreRejected() throws Exception {
        String auth = newUserAuth();
        mvc.perform(post("/api/chat").header("Authorization", auth).contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"message":"  "}"""))
                .andExpect(status().isBadRequest());
        mvc.perform(post("/api/chat").header("Authorization", auth).contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"message":"%s"}""".formatted("가".repeat(501))))
                .andExpect(status().isBadRequest());
        mvc.perform(post("/api/chat").contentType(MediaType.APPLICATION_JSON).content("""
                        {"message":"질문"}"""))
                .andExpect(status().isUnauthorized());
        assertThat(calls.get()).isZero();
    }
}
