// 로그인 토큰 보관. 브라우저 localStorage 에 두는 단순한 방식이다(XSS 에 노출될 수 있어서 운영에서는
// httpOnly 쿠키를 쓰는 BFF 구조가 더 안전하다). 저장소를 못 쓰는 환경(시크릿 창 등)에서도 앱이 죽지 않게 try/catch 로 감싼다.
const KEY = "sesac.accessToken";

export function getToken(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(KEY, token);
  } catch {}
}

export function clearToken(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {}
}
