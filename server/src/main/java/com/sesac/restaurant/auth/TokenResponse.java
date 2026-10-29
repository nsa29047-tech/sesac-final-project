package com.sesac.restaurant.auth;

public record TokenResponse(String accessToken, String tokenType, long expiresIn) {
}
