package com.sesac.restaurant.auth;

import com.sesac.restaurant.config.JwtProperties;
import com.sesac.restaurant.config.SecurityConfig;
import com.sesac.restaurant.domain.User;
import org.junit.jupiter.api.Test;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.security.oauth2.jwt.JwsHeader;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.Duration;
import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Spring 컨텍스트·DB 없이 토큰 발급과 검증만 확인한다. */
class JwtServiceTest {

    private static final String SECRET = "test-only-secret-test-only-secret-0123";

    private final SecurityConfig config = new SecurityConfig();

    private JwtService serviceWith(JwtProperties props) {
        return new JwtService(config.jwtEncoder(config.jwtSecretKey(props)), props);
    }

    private JwtDecoder decoderWith(JwtProperties props) {
        return config.jwtDecoder(config.jwtSecretKey(props));
    }

    private static User user() {
        User user = new User("a@example.com", "hash", "nick");
        ReflectionTestUtils.setField(user, "id", 7L);
        return user;
    }

    @Test
    void issuedTokenCanBeDecodedWithSubjectAndExpiry() {
        JwtProperties props = new JwtProperties(SECRET, Duration.ofHours(1));
        TokenResponse token = serviceWith(props).issue(user());

        Jwt jwt = decoderWith(props).decode(token.accessToken());
        assertThat(jwt.getSubject()).isEqualTo("7");
        assertThat(jwt.<String>getClaim("email")).isEqualTo("a@example.com");
        assertThat(jwt.getExpiresAt()).isAfter(Instant.now().plus(Duration.ofMinutes(59)));
        assertThat(token.tokenType()).isEqualTo("Bearer");
        assertThat(token.expiresIn()).isEqualTo(3600);
    }

    @Test
    void tokenSignedWithAnotherKeyIsRejected() {
        TokenResponse token = serviceWith(new JwtProperties(SECRET, Duration.ofHours(1))).issue(user());
        JwtProperties other = new JwtProperties("another-secret-another-secret-012345", Duration.ofHours(1));

        assertThatThrownBy(() -> decoderWith(other).decode(token.accessToken())).isInstanceOf(Exception.class);
    }

    @Test
    void expiredTokenIsRejected() {
        JwtProperties props = new JwtProperties(SECRET, Duration.ofHours(1));
        // 인코더는 이미 만료된 토큰을 만들어 주지 않으므로 발급 시각을 과거로 지정해 만든다(검증기의 시계 오차 허용 60초보다 더 과거)
        Instant issuedAt = Instant.now().minus(Duration.ofMinutes(10));
        JwtClaimsSet claims = JwtClaimsSet.builder().subject("7")
                .issuedAt(issuedAt).expiresAt(issuedAt.plus(Duration.ofMinutes(5))).build();
        String token = config.jwtEncoder(config.jwtSecretKey(props))
                .encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(), claims))
                .getTokenValue();

        assertThatThrownBy(() -> decoderWith(props).decode(token)).isInstanceOf(Exception.class);
    }

    @Test
    void shortSecretFailsFast() {
        assertThatThrownBy(() -> config.jwtSecretKey(new JwtProperties("too-short", Duration.ofHours(1))))
                .isInstanceOf(IllegalStateException.class);
    }
}
