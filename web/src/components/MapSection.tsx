"use client";

import dynamic from "next/dynamic";
import type { Restaurant } from "@/lib/types";

// Leaflet 은 window 가 필요해서 서버 렌더링에서 뺀다(ssr:false 는 클라이언트 컴포넌트에서만 쓸 수 있다).
const RestaurantMap = dynamic(() => import("./RestaurantMap"), {
  ssr: false,
  loading: () => <div className="h-80 w-full animate-pulse rounded-2xl bg-stone-200" aria-label="지도 불러오는 중" />,
});

export default function MapSection({ restaurants, extra, onDetail }: { restaurants: Restaurant[]; extra?: Restaurant[]; onDetail?: (r: Restaurant) => void }) {
  return <RestaurantMap restaurants={restaurants} extra={extra} onDetail={onDetail} />;
}
