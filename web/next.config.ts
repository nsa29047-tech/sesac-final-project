import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  // Docker 이미지(web/Dockerfile)를 만들 때만 최소 실행 파일(.next/standalone)로 묶는다.
  // 평소의 `next start`(web-test 등)는 standalone 출력과 같이 쓸 수 없어서 환경변수가 있을 때만 켠다.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
