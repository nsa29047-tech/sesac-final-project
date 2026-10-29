package com.sesac.restaurant.auth;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record SignupRequest(
        @NotBlank @Email @Size(max = 255) String email,
        // BCrypt 는 72바이트까지만 쓰므로 상한을 둔다
        @NotBlank @Size(min = 8, max = 72) String password,
        @NotBlank @Size(max = 50) String nickname) {
}
