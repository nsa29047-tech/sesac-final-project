package com.sesac.restaurant;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.boot.test.context.SpringBootTest;

// DB 접속이 필요해서 APP_DB_URL 이 있을 때만 돈다
@EnabledIfEnvironmentVariable(named = "APP_DB_URL", matches = ".+")
@SpringBootTest(properties = "app.jwt.secret=test-only-secret-test-only-secret-0123")
class RestaurantServerApplicationTests {

	@Test
	void contextLoads() {
	}

}
