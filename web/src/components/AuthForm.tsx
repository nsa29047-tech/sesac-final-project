"use client";

import { UtensilsCrossed } from "lucide-react";
import { useState } from "react";
import { ApiError, fetchMe, login, signup } from "@/lib/api";
import { setToken } from "@/lib/auth";
import type { User } from "@/lib/types";

const FIELD =
  "w-full rounded-xl border border-neutral-300 bg-white px-4 py-2.5 text-[15px] outline-none placeholder:text-neutral-400 focus:border-brand focus:ring-4 focus:ring-brand-soft";

/** 로그인·회원가입 화면. 가입에 성공하면 바로 로그인까지 한다. */
export default function AuthForm({ notice, onLogin }: { notice?: string; onLogin: (user: User) => void }) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      if (mode === "signup") await signup(email.trim(), password, nickname.trim());
      setToken(await login(email.trim(), password));
      onLogin(await fetchMe());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "알 수 없는 오류가 났어요.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand text-white">
            <UtensilsCrossed size={24} aria-hidden />
          </span>
          <h1 className="text-[24px] font-extrabold tracking-tight text-neutral-900">맛집 노트</h1>
          <p className="text-[14px] text-neutral-500">미식 유튜브 속 식당을 찾아 드려요.</p>
        </div>

        <div className="rounded-[20px] border border-[#e9e4dd] bg-white p-6 shadow-[0_1px_2px_rgba(60,40,20,0.04)]">
          <div className="mb-5 grid grid-cols-2 gap-1 rounded-full bg-neutral-100 p-1 text-[14px] font-semibold">
            {(["login", "signup"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => {
                  setMode(m);
                  setError("");
                }}
                className={`rounded-full py-1.5 ${mode === m ? "bg-white text-neutral-900 shadow-sm" : "text-neutral-500"}`}
              >
                {m === "login" ? "로그인" : "회원가입"}
              </button>
            ))}
          </div>

          {notice && <p className="mb-4 rounded-xl bg-brand-soft px-4 py-2.5 text-[13px] text-brand-ink">{notice}</p>}

          <form onSubmit={submit} className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-[13px] font-semibold text-neutral-600">이메일</span>
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" maxLength={255} className={FIELD} placeholder="you@example.com" />
            </label>
            {mode === "signup" && (
              <label className="block">
                <span className="mb-1 block text-[13px] font-semibold text-neutral-600">닉네임</span>
                <input required value={nickname} onChange={(e) => setNickname(e.target.value)} maxLength={50} className={FIELD} placeholder="표시될 이름" />
              </label>
            )}
            <label className="block">
              <span className="mb-1 block text-[13px] font-semibold text-neutral-600">비밀번호</span>
              <input
                type="password"
                required
                minLength={mode === "signup" ? 8 : undefined}
                maxLength={72}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
                className={FIELD}
                placeholder={mode === "signup" ? "8자 이상" : "비밀번호"}
              />
            </label>

            {error && (
              <p role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-[13px] text-red-700">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-xl bg-brand py-2.5 text-[15px] font-bold text-white hover:bg-brand-dark disabled:bg-neutral-300"
            >
              {busy ? "처리 중…" : mode === "login" ? "로그인" : "가입하고 시작하기"}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}
