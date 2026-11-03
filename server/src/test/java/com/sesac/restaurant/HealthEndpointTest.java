package com.sesac.restaurant;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/** 컨테이너 헬스체크용 엔드포인트(APP_DB_URL 이 있을 때만 실행: DB 연결 상태도 함께 본다). */
@SpringBootTest(properties = "app.jwt.secret=test-only-secret-test-only-secret-0123")
@AutoConfigureMockMvc
@EnabledIfEnvironmentVariable(named = "APP_DB_URL", matches = ".+")
class HealthEndpointTest {

    @Autowired MockMvc mvc;

    @Test
    void healthIsPublicAndUpWithoutDetails() throws Exception {
        mvc.perform(get("/actuator/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UP"))
                .andExpect(jsonPath("$.components").doesNotExist());   // 내부 정보는 노출하지 않는다
    }

    @Test
    void otherActuatorEndpointsAreNotExposed() throws Exception {
        mvc.perform(get("/actuator/env")).andExpect(status().is4xxClientError());
        mvc.perform(get("/actuator/beans")).andExpect(status().is4xxClientError());
    }
}
