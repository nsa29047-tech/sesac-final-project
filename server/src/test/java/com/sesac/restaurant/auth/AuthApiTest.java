package com.sesac.restaurant.auth;

import com.jayway.jsonpath.JsonPath;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.transaction.annotation.Transactional;

import java.util.UUID;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** 회원가입 -> 로그인 -> 인증이 필요한 API 흐름(APP_DB_URL 이 있을 때만 실행, 각 테스트는 롤백된다). */
@SpringBootTest(properties = "app.jwt.secret=test-only-secret-test-only-secret-0123")
@AutoConfigureMockMvc
@Transactional
@EnabledIfEnvironmentVariable(named = "APP_DB_URL", matches = ".+")
class AuthApiTest {

    private static final String PASSWORD = "password-1234";

    @Autowired MockMvc mvc;

    private static String email() {
        return "t-" + UUID.randomUUID() + "@example.com";
    }

    private void signup(String email) throws Exception {
        mvc.perform(post("/api/auth/signup").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"%s","password":"%s","nickname":"tester"}""".formatted(email, PASSWORD)))
                .andExpect(status().isCreated());
    }

    private String login(String email, String password) throws Exception {
        String body = mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"%s","password":"%s"}""".formatted(email, password)))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return JsonPath.read(body, "$.accessToken");
    }

    @Test
    void signupLoginAndMe() throws Exception {
        String email = email();
        signup(email);
        String token = login(email, PASSWORD);

        mvc.perform(get("/api/me").header("Authorization", "Bearer " + token))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.email").value(email))
                .andExpect(jsonPath("$.nickname").value("tester"))
                .andExpect(jsonPath("$.passwordHash").doesNotExist());
    }

    @Test
    void emailIsCaseInsensitive() throws Exception {
        String email = email();
        signup(email);
        login(email.toUpperCase(), PASSWORD);
    }

    @Test
    void duplicateEmailIs409() throws Exception {
        String email = email();
        signup(email);
        mvc.perform(post("/api/auth/signup").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"%s","password":"%s","nickname":"x"}""".formatted(email, PASSWORD)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("DUPLICATE_EMAIL"));
    }

    @Test
    void invalidSignupIs400() throws Exception {
        mvc.perform(post("/api/auth/signup").contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"email":"not-an-email","password":"short","nickname":""}"""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_REQUEST"));
    }

    @Test
    void wrongPasswordAndUnknownEmailGiveTheSame401() throws Exception {
        String email = email();
        signup(email);
        for (String[] c : new String[][]{{email, "wrong-password"}, {email(), PASSWORD}}) {
            mvc.perform(post("/api/auth/login").contentType(MediaType.APPLICATION_JSON)
                            .content("""
                                    {"email":"%s","password":"%s"}""".formatted(c[0], c[1])))
                    .andExpect(status().isUnauthorized())
                    .andExpect(jsonPath("$.code").value("INVALID_CREDENTIALS"));
        }
    }

    @Test
    void meRequiresValidToken() throws Exception {
        mvc.perform(get("/api/me")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/me").header("Authorization", "Bearer abc.def.ghi")).andExpect(status().isUnauthorized());
    }
}
