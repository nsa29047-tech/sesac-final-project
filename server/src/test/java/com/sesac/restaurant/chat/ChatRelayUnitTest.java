package com.sesac.restaurant.chat;

import com.sesac.restaurant.common.ApiException;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import tools.jackson.databind.json.JsonMapper;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** DB·Spring 없이 SSE 파서, 업스트림 연결 실패, 동시 스트림 제한을 확인한다. */
class ChatRelayUnitTest {

    private static List<SseFrame> parse(String text) throws IOException {
        List<SseFrame> frames = new ArrayList<>();
        SseFrameReader.read(new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8)), frames::add);
        return frames;
    }

    @Test
    void parsesFramesInOrderWithKorean() throws IOException {
        List<SseFrame> frames = parse("""
                event: thread
                data: {"thread_id": "t1"}

                event: token
                data: {"text": "강남역 일식집"}

                """);
        assertThat(frames).containsExactly(
                new SseFrame("thread", "{\"thread_id\": \"t1\"}"),
                new SseFrame("token", "{\"text\": \"강남역 일식집\"}"));
    }

    @Test
    void joinsMultipleDataLinesIgnoresCommentsAndDefaultsEventName() throws IOException {
        List<SseFrame> frames = parse(": keep-alive\ndata: a\ndata: b\n\n");
        assertThat(frames).containsExactly(new SseFrame("message", "a\nb"));
    }

    @Test
    void dropsIncompleteTrailingFrame() throws IOException {
        // 마지막 이벤트가 빈 줄 없이 끊겼다 = 불완전하므로 넘기지 않는다
        List<SseFrame> frames = parse("event: token\ndata: {\"text\":\"a\"}\n\nevent: token\ndata: {\"text\":\"b\"}\n");
        assertThat(frames).hasSize(1);
    }

    @Test
    void unreachableUpstreamIs502() {
        ChatUpstream upstream = new ChatUpstream(HttpClient.newHttpClient(),
                new ChatProperties("http://127.0.0.1:1", 5), JsonMapper.builder().build());

        assertThatThrownBy(() -> upstream.open(UUID.randomUUID(), "질문"))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.getStatus()).isEqualTo(HttpStatus.BAD_GATEWAY);
                    assertThat(e.getCode()).isEqualTo("CHAT_UNAVAILABLE");
                });
    }

    @Test
    void rejectsWith503WhenNoStreamSlotIsFree() {
        // 슬롯이 0개면 DB·업스트림을 건드리기 전에 거절한다(그래서 의존성은 null 이어도 된다)
        ChatRelayService relay = new ChatRelayService(null, null, JsonMapper.builder().build(),
                new ChatProperties("http://unused", 0));

        assertThatThrownBy(() -> relay.relay(1L, null, "질문"))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.getStatus()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
                    assertThat(e.getCode()).isEqualTo("CHAT_OVERLOADED");
                });
    }
}
