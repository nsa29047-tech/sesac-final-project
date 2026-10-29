package com.sesac.restaurant.chat;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.net.http.HttpClient;
import java.time.Duration;

@Configuration
@EnableConfigurationProperties(ChatProperties.class)
public class ChatConfig {

    /** HTTP/1.1 로 고정한다(http:// 주소에서 HTTP/2 업그레이드를 시도하지 않게). 응답 읽기 제한 시간은 FastAPI(60초)와 SseEmitter(90초)가 맡는다. */
    @Bean
    HttpClient chatHttpClient() {
        return HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1)
                .connectTimeout(Duration.ofSeconds(3))
                .build();
    }
}
