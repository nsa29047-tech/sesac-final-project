import type { Restaurant } from "@/lib/types";
import { formatPrice, michelinBadge, shortRegion } from "@/lib/format";

/** 추천 식당 한 곳의 카드. 번호는 지도 마커 번호와 같다. */
export default function RestaurantCard({ r, num, onDetail }: { r: Restaurant; num: number; onDetail?: (r: Restaurant) => void }) {
  const video = r.videos[0];
  const cat = (r.category_detail || r.category_broad || "").trim();
  const sub = [shortRegion(r.region), cat.length > 22 ? cat.slice(0, 21) + "…" : cat].filter(Boolean).join(" · ");
  const quote = r.matched[0]?.content.replace(/\n/g, " ");

  return (
    <article className="flex flex-col overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-sm">
      <div
        className="relative h-32 bg-gradient-to-br from-[#ffb199] to-brand bg-cover bg-center"
        style={video ? { backgroundImage: `url('${video.thumbnail}')` } : undefined}
      >
        {r.michelin && (
          <span className="absolute left-2.5 top-2.5 rounded-full bg-white px-2.5 py-0.5 text-xs font-bold text-red-700 shadow">
            {michelinBadge(r.michelin.label)}
          </span>
        )}
        <span className="absolute right-2.5 top-2.5 flex h-[26px] w-[26px] items-center justify-center rounded-full border-2 border-white bg-brand text-[13px] font-extrabold text-white shadow">
          {num}
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-1.5 p-3.5">
        <h3 className="text-base font-bold leading-snug text-neutral-900">{r.name}</h3>
        {sub && <p className="text-[12.5px] text-neutral-500">{sub}</p>}

        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] text-neutral-800">
          {r.rating != null && (
            <span>
              <b className="text-brand">★ {r.rating}</b>
              {r.rating_count != null && <span className="text-neutral-400"> ({r.rating_count.toLocaleString()})</span>}
            </span>
          )}
          {r.price_level ? (
            <span className="tracking-wider text-neutral-300" aria-label={`가격대 ${r.price_level}/4`}>
              <b className="text-brand">{"●".repeat(r.price_level)}</b>
              {"●".repeat(4 - r.price_level)}
            </span>
          ) : null}
          {r.price && <span className="text-[12.5px] text-neutral-500">{formatPrice(r.price)}</span>}
        </div>

        {r.chef_name && (
          <div>
            <span className="rounded-full bg-brand-soft px-2 py-0.5 text-[11.5px] text-brand-dark">👨‍🍳 {r.chef_name}</span>
          </div>
        )}

        {quote && (
          <p className="rounded-lg bg-stone-100 px-2.5 py-2 text-[12.5px] leading-snug text-neutral-600">
            “{quote.length > 90 ? quote.slice(0, 90) + "…" : quote}”
          </p>
        )}

        {onDetail && (
          <button
            type="button"
            onClick={() => onDetail(r)}
            className="mt-auto rounded-[10px] border border-neutral-300 bg-white py-1.5 text-[12.5px] font-semibold text-neutral-700 hover:bg-neutral-50"
          >
            상세 정보 보기
          </button>
        )}
        <div className={`flex gap-2 ${onDetail ? "" : "mt-auto"} pt-1.5`}>
          {video && (
            <a
              href={video.url}
              target="_blank"
              rel="noreferrer"
              className="flex-1 rounded-[10px] border border-brand bg-brand py-1.5 text-center text-[12.5px] font-semibold text-white hover:bg-brand-dark"
            >
              영상 보기
            </a>
          )}
          {r.google_maps_url && (
            <a
              href={r.google_maps_url}
              target="_blank"
              rel="noreferrer"
              className="flex-1 rounded-[10px] border border-brand-line bg-white py-1.5 text-center text-[12.5px] font-semibold text-brand-dark hover:bg-brand-soft"
            >
              지도
            </a>
          )}
        </div>
      </div>
    </article>
  );
}
