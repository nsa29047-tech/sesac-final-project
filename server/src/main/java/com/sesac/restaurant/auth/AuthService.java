package com.sesac.restaurant.auth;

import com.sesac.restaurant.common.ApiException;
import com.sesac.restaurant.domain.User;
import com.sesac.restaurant.repository.UserRepository;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Locale;

@Service
public class AuthService {

    private final UserRepository users;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;
    /** 없는 이메일로 로그인할 때도 해시 비교를 한 번 해서, 응답 시간으로 가입 여부를 알아내지 못하게 한다. */
    private final String dummyHash;

    public AuthService(UserRepository users, PasswordEncoder passwordEncoder, JwtService jwtService) {
        this.users = users;
        this.passwordEncoder = passwordEncoder;
        this.jwtService = jwtService;
        this.dummyHash = passwordEncoder.encode("dummy-password-for-timing");
    }

    @Transactional
    public UserResponse signup(SignupRequest request) {
        String email = normalize(request.email());
        if (users.existsByEmail(email)) {
            throw duplicateEmail();
        }
        try {
            User saved = users.saveAndFlush(
                    new User(email, passwordEncoder.encode(request.password()), request.nickname().trim()));
            return UserResponse.from(saved);
        } catch (DataIntegrityViolationException e) {
            // existsByEmail 와 저장 사이에 같은 이메일이 먼저 들어온 경우(UNIQUE 제약이 마지막 방어선)
            throw duplicateEmail();
        }
    }

    @Transactional(readOnly = true)
    public TokenResponse login(LoginRequest request) {
        User user = users.findByEmail(normalize(request.email())).orElse(null);
        String hash = user != null ? user.getPasswordHash() : dummyHash;
        boolean matches = passwordEncoder.matches(request.password(), hash);
        if (user == null || !matches) {
            // 이메일이 없는 경우와 비밀번호가 틀린 경우를 같은 응답으로 돌려준다
            throw new ApiException(HttpStatus.UNAUTHORIZED, "INVALID_CREDENTIALS", "이메일 또는 비밀번호가 올바르지 않습니다");
        }
        return jwtService.issue(user);
    }

    private static String normalize(String email) {
        return email.trim().toLowerCase(Locale.ROOT);
    }

    private static ApiException duplicateEmail() {
        return new ApiException(HttpStatus.CONFLICT, "DUPLICATE_EMAIL", "이미 가입된 이메일입니다");
    }
}
