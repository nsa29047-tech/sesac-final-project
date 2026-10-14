import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "맛집 노트",
  description: "미식 유튜브 영상에서 뽑은 식당으로 맛집을 추천해 드려요.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className="h-full antialiased">
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
