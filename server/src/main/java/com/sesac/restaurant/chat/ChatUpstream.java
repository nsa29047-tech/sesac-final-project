package com.sesac.restaurant.chat;

import com.sesac.restaurant.common.ApiException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.Map;
import java.util.UUID;

/** FastAPI 챗봇 서버(POST /api/chat)를 호출한다. 응답 헤더까지만 기다리고 본문은 스트림으로 넘긴다. */
@Component
public class ChatUpstream {

    private final HttpClient client;
    private final ChatProperties props;
    private final JsonMapper mapper;

    public ChatUpstream(HttpClient chatHttpClient, ChatProperties props, JsonMapper mapper) {
        this.client = chatHttpClient;
        this.props = props;
        this.mapper = mapper;
    }

    /**
     * 질문을 보내고 SSE 본문 스트림을 돌려준다. 호출한 쪽이 반드시 닫아야 한다(닫으면 요청이 취소된다).
     * 200 이 아니면 스트림을 닫고 ApiException 으로 바꿔 던진다.
     */
    public InputStream open(UUID threadId, String message) {
        String body = mapper.writeValueAsString(Map.of("thread_id", threadId.toString(), "message", message));
        HttpRequest request = HttpRequest.newBuilder(URI.create(props.baseUrl() + "/api/chat"))
                .header("Content-Type", "application/json")
                .header("Accept", "text/event-stream")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        HttpResponse<InputStream> response;
        try {
            response = client.send(request, HttpResponse.BodyHandlers.ofInputStream());
        } catch (IOException e) {
            throw unavailable();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw unavailable();
        }
        int status = response.statusCode();
        if (status == 200) {
            return response.body();
        }
        try {
            response.body().close();
        } catch (IOException ignored) {
            // 이미 실패 응답이라 닫기 실패는 무시한다
        }
        if (status == 409) {
            throw new ApiException(HttpStatus.CONFLICT, "CHAT_BUSY", "이 대화는 이미 답변 중입니다");
        }
        if (status == 422) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", "챗봇 서버가 요청을 받아들이지 않았습니다");
        }
        throw unavailable();
    }

    /** 식당 상세(GET /api/restaurants/{id})를 그대로 받아 JSON 문자열로 돌려준다. 없는 식당이면 404. */
    public String restaurantDetail(long restaurantId) {
        HttpRequest request = HttpRequest.newBuilder(URI.create(props.baseUrl() + "/api/restaurants/" + restaurantId))
                .header("Accept", "application/json")
                .timeout(java.time.Duration.ofSeconds(15))
                .GET()
                .build();
        HttpResponse<String> response;
        try {
            response = client.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (IOException e) {
            throw unavailable();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw unavailable();
        }
        if (response.statusCode() == 200) {
            return response.body();
        }
        if (response.statusCode() == 404) {
            throw new ApiException(HttpStatus.NOT_FOUND, "RESTAURANT_NOT_FOUND", "식당 정보를 찾지 못했습니다");
        }
        throw unavailable();
    }

    private static ApiException unavailable() {
        return new ApiException(HttpStatus.BAD_GATEWAY, "CHAT_UNAVAILABLE", "챗봇 서버에 연결할 수 없습니다");
    }
}
