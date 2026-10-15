import type { Restaurant } from "@/lib/types";
import MapSection from "./MapSection";
import RestaurantCard from "./RestaurantCard";

/** 추천 결과(지도와 카드). 넓은 화면에서는 채팅 옆 패널로, 좁은 화면에서는 답변 아래에 그대로 쓴다. */
export default function ResultsList({ cards, mapExtra = [], onDetail }: { cards: Restaurant[]; mapExtra?: Restaurant[]; onDetail: (r: Restaurant) => void }) {
  return (
    <div className="space-y-4">
      <MapSection restaurants={cards} extra={mapExtra} onDetail={onDetail} />
      <div className="grid gap-3.5 [grid-template-columns:repeat(auto-fill,minmax(230px,1fr))]">
        {cards.map((r, i) => (
          <RestaurantCard key={r.restaurant_id} r={r} num={i + 1} onDetail={onDetail} />
        ))}
      </div>
    </div>
  );
}
