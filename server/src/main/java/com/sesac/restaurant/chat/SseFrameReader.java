package com.sesac.restaurant.chat;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

/** text/event-stream 을 읽어 빈 줄로 끝나는 이벤트 단위로 넘긴다. 스트림이 끝날 때까지 블로킹한다. */
final class SseFrameReader {

    @FunctionalInterface
    interface FrameHandler {
        void accept(SseFrame frame) throws IOException;
    }

    private SseFrameReader() {
    }

    /** 마지막 이벤트가 빈 줄 없이 끊겼으면 불완전한 것이므로 넘기지 않는다. */
    static void read(InputStream in, FrameHandler handler) throws IOException {
        BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        String event = null;
        StringBuilder data = null;
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.isEmpty()) {
                if (data != null) {
                    handler.accept(new SseFrame(event != null ? event : "message", data.toString()));
                }
                event = null;
                data = null;
                continue;
            }
            if (line.startsWith(":")) {
                continue;   // 주석(keep-alive)
            }
            int colon = line.indexOf(':');
            String field = colon < 0 ? line : line.substring(0, colon);
            String value = colon < 0 ? "" : line.substring(colon + 1);
            if (value.startsWith(" ")) {
                value = value.substring(1);
            }
            if (field.equals("event")) {
                event = value;
            } else if (field.equals("data")) {
                if (data == null) {
                    data = new StringBuilder();
                } else {
                    data.append('\n');
                }
                data.append(value);
            }
        }
    }
}
