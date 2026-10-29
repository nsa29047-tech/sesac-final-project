"use client";

import { useEffect, useState } from "react";
import { fetchMe, setUnauthorizedHandler } from "@/lib/api";
import { clearToken, getToken } from "@/lib/auth";
import type { User } from "@/lib/types";
import AuthForm from "./AuthForm";
import Chat from "./Chat";

type AuthState = { status: "loading" } | { status: "out"; notice?: string } | { status: "in"; user: User };

/** 로그인 상태에 따라 로그인 화면과 챗봇 화면을 나눈다. 토큰은 브라우저에만 있어서 서버 렌더링 때는 알 수 없다(loading). */
export default function App() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    // 어느 요청이든 401 을 받으면(토큰 만료·위조) 로그인 화면으로 돌아간다
    setUnauthorizedHandler(() => {
      clearToken();
      setAuth({ status: "out", notice: "로그인이 만료됐어요. 다시 로그인해 주세요." });
    });
    // 저장된 토큰이 아직 유효한지 서버에 확인한다(없으면 바로 로그인 화면). 상태는 콜백 안에서만 바꾼다
    Promise.resolve(getToken() ? fetchMe() : null)
      .then((user) => setAuth((s) => (s.status !== "loading" ? s : user ? { status: "in", user } : { status: "out" })))
      .catch(() => setAuth((s) => (s.status === "loading" ? { status: "out" } : s)));
    return () => setUnauthorizedHandler(null);
  }, []);

  function logout() {
    clearToken();
    setAuth({ status: "out" });
  }

  if (auth.status === "loading") {
    return <div className="flex h-dvh items-center justify-center text-sm text-neutral-400">불러오는 중…</div>;
  }
  if (auth.status === "out") {
    return <AuthForm notice={auth.notice} onLogin={(user) => setAuth({ status: "in", user })} />;
  }
  return <Chat user={auth.user} onLogout={logout} />;
}
