package com.sesac.restaurant.chat;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 식당 상세 팝업용. 상세 조립(영상 노트·메뉴·영업시간)은 챗봇 서버가 하므로 로그인을 확인하고 응답 JSON 을 그대로 전달한다.
 * 프런트가 챗봇 서버를 직접 부르지 않고 Spring 서버 하나만 보게 하려는 용도다.
 */
@RestController
@RequestMapping("/api/restaurants")
public class RestaurantController {

    private final ChatUpstream upstream;

    public RestaurantController(ChatUpstream upstream) {
        this.upstream = upstream;
    }

    @GetMapping("/{restaurantId}")
    public ResponseEntity<String> detail(@PathVariable long restaurantId) {
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).body(upstream.restaurantDetail(restaurantId));
    }
}
