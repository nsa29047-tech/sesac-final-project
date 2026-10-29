package com.sesac.restaurant.common;

import org.springframework.http.HttpStatus;

/** 컨트롤러·서비스에서 던지면 GlobalExceptionHandler 가 {code, message} 응답으로 바꾼다. */
public class ApiException extends RuntimeException {

    private final HttpStatus status;
    private final String code;

    public ApiException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public HttpStatus getStatus() {
        return status;
    }

    public String getCode() {
        return code;
    }
}
