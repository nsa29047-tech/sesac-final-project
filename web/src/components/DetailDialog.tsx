"use client";

// 식당 상세 팝업: 영상 속 메뉴 타임라인(플레이어) -> 영상 소개·총평 -> 음료 -> 기본 정보(주소·가격·영업시간) -> 위치 지도.
// 브라우저 기본 <dialog> 를 쓴다(ESC 로 닫기, 바깥 클릭으로 닫기, 포커스 가둠은 브라우저가 처리).
import { useEffect, useRef, useState } from "react";
import { ApiError, fetchDetail } from "@/lib/api";
import { formatPrice, hoursGroups, michelinBadge, shortRegion } from "@/lib/format";
import type { RestaurantDetail } from "@/lib/types";
import MapSection from "./MapSection";
import VideoPlayer from "./VideoPlayer";

const cache = new Map<number, RestaurantDetail>(); // 같은 식당을 다시 열 때 서버를 또 부르지 않는다(새로고침하면 사라짐)

function Section({ icon, title, children }: { icon: string; title: string; children: React.ReactNode }) {
  return (
    <section className="mt-7">
      <h3 className="mb-2.5 text-base font-extrabold text-neutral-900">
        {icon} {title}
      </h3>
      {children}
    </section>
  );
}

function Detail({ d }: { d: RestaurantDetail }) {
  const cat = d.category_detail || d.category_broad || "";
  const sub = [shortRegion(d.region), cat].filter(Boolean).join(" · ");
  const foodNotes = d.notes.filter((n) => d.menus.some((m) => m.video_id === n.video_id && m.item_type === "FOOD"));
  const drinks = d.menus.filter((m) => m.item_type === "DRINK");
  const hours = hoursGroups(d.hours);

  const rows: [string, string][] = [];
  if (d.address) rows.push(["주소", d.address]);
  if (d.phone) rows.push(["전화", d.phone]);
  if (d.price) rows.push(["가격", `${formatPrice(d.price)} (1인 기준)`]);
  else if (d.price_level) rows.push(["가격대", `${"●".repeat(d.price_level)}${"○".repeat(4 - d.price_level)} (Google 기준 단계)`]);
  else rows.push(["가격대", "정보 없음"]);

  return (
    <div>
      {sub && <p className="-mt-1 text-sm text-neutral-500">{sub}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[15px]">
        {d.michelin && (
          <span className="rounded-full bg-white px-2.5 py-0.5 text-xs font-bold text-red-700 shadow ring-1 ring-neutral-200">
            {michelinBadge(d.michelin.label)}
          </span>
        )}
        {d.rating != null && (
          <span>
            <b className="text-brand">★ {d.rating}</b>
            {d.rating_count != null && <span className="text-sm text-neutral-400"> ({d.rating_count.toLocaleString()})</span>}
          </span>
        )}
        {d.price_level ? (
          <span className="tracking-wider text-neutral-300">
            <b className="text-brand">{"●".repeat(d.price_level)}</b>
            {"●".repeat(4 - d.price_level)}
          </span>
        ) : null}
        {d.chef_name && <span className="rounded-full bg-brand-soft px-2.5 py-0.5 text-xs text-brand-dark">👨‍🍳 {d.chef_name}</span>}
      </div>
      {d.tags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {d.tags.slice(0, 8).map((t) => (
            <span key={t} className="rounded-full bg-brand-soft px-2.5 py-0.5 text-[12.5px] text-brand-dark">
              #{t}
            </span>
          ))}
        </div>
      )}

      <Section icon="🎬" title="영상에 나온 메뉴 타임라인">
        {foodNotes.length > 0 ? (
          <details open className="group">
            <summary className="mb-2 cursor-pointer text-sm font-semibold text-neutral-600">접기 / 펼치기</summary>
            <div className="space-y-6">
              {foodNotes.map((n) => (
                <div key={n.video_id}>
                  {d.notes.length > 1 && <p className="mb-2 text-[13px] text-neutral-500">📺 {n.title}</p>}
                  <VideoPlayer videoId={n.video_id} url={n.url} menus={d.menus.filter((m) => m.video_id === n.video_id)} />
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs leading-relaxed text-neutral-400">
              메뉴를 누르면 그 장면부터 재생돼요. 시각은 자막에서 추정한 값이라 실제 화면과 몇 초 차이가 날 수 있어요. 메뉴별 맛 후기는 저장하지 않아 조리 특징 설명만 보여 줘요.
            </p>
          </details>
        ) : (
          <p className="rounded-xl bg-sky-50 px-4 py-3 text-sm text-sky-800">이 식당은 영상 속 메뉴 정보가 아직 없어요.</p>
        )}
      </Section>

      {d.notes.length > 0 && (
        <Section icon="📝" title="영상 속 소개와 총평">
          {d.notes.map((n) => (
            <div key={n.video_id} className="mb-2.5 rounded-2xl bg-[#faf7f4] p-4">
              <p className="mb-2.5 text-[13px] text-neutral-500">
                <b className="mr-1.5 text-brand">▶</b>
                <a href={n.url} target="_blank" rel="noreferrer" className="border-b border-neutral-300 hover:text-neutral-700">
                  {n.title}
                </a>
                {n.course_name && n.course_name !== "코스" ? ` · 코스: ${n.course_name}` : n.is_course ? " · 코스 요리" : ""}
              </p>
              <dl className="grid grid-cols-[62px_1fr] gap-x-3 gap-y-2 text-sm leading-relaxed">
                {n.concept && (
                  <>
                    <dt className="pt-0.5 text-[12.5px] font-semibold text-neutral-400">컨셉</dt>
                    <dd className="break-words text-neutral-800">{n.concept}</dd>
                  </>
                )}
                {n.atmosphere && (
                  <>
                    <dt className="pt-0.5 text-[12.5px] font-semibold text-neutral-400">분위기</dt>
                    <dd className="break-words text-neutral-800">{n.atmosphere}</dd>
                  </>
                )}
              </dl>
              {n.key_points.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {n.key_points.map((k) => (
                    <span key={k} className="rounded-full bg-brand-soft px-2.5 py-0.5 text-[12.5px] text-brand-dark">
                      {k}
                    </span>
                  ))}
                </div>
              )}
              {n.final_review && (
                <blockquote className="mt-3.5 border-l-[3px] border-brand pl-3.5 text-[14.5px] leading-relaxed text-neutral-700">
                  {n.final_review}
                  <small className="mt-0.5 block text-[11.5px] text-neutral-400">영상 속 총평</small>
                </blockquote>
              )}
            </div>
          ))}
        </Section>
      )}

      {d.chef_info && (
        <p className="mt-3 text-xs leading-relaxed text-neutral-400">👨‍🍳 셰프 소개 · {d.chef_info} (영상에서 한 말이라 검증된 사실은 아니에요)</p>
      )}

      {drinks.length > 0 && (
        <Section icon="🍷" title="음료">
          <ul className="rounded-2xl border border-neutral-200 bg-white px-4">
            {drinks.map((m, i) => (
              <li key={`${m.name}-${i}`} className="flex gap-2.5 border-b border-neutral-100 py-2.5 last:border-b-0">
                <span>🍷</span>
                <div>
                  <b className="text-sm text-neutral-900">{m.name}</b>
                  {m.description && <p className="text-[13px] leading-relaxed text-neutral-500">{m.description}</p>}
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section icon="📍" title="기본 정보">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-2xl border border-neutral-200 bg-white p-4">
            <dl className="grid grid-cols-[62px_1fr] gap-x-3 gap-y-2 text-sm leading-relaxed">
              {rows.map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="pt-0.5 text-[12.5px] font-semibold text-neutral-400">{k}</dt>
                  <dd className="break-words text-neutral-800">{v}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-3 flex flex-wrap gap-2">
              {d.website && (
                <a href={d.website} target="_blank" rel="noreferrer" className="rounded-full border border-brand-line px-3.5 py-1.5 text-[13px] font-semibold text-brand-dark hover:bg-brand-soft">
                  웹사이트
                </a>
              )}
              {d.google_maps_url && (
                <a href={d.google_maps_url} target="_blank" rel="noreferrer" className="rounded-full border border-brand-line px-3.5 py-1.5 text-[13px] font-semibold text-brand-dark hover:bg-brand-soft">
                  구글 지도
                </a>
              )}
            </div>
          </div>
          <div className="rounded-2xl border border-neutral-200 bg-white p-4">
            {d.hours.length > 0 ? (
              <dl className="grid grid-cols-[62px_1fr] gap-x-3 gap-y-2 text-sm">
                {hours.map(([label, text]) => (
                  <div key={label} className="contents">
                    <dt className="font-bold text-neutral-700">{label}</dt>
                    <dd className={text === "휴무" ? "text-neutral-300" : "text-neutral-800"}>{text}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-xs text-neutral-400">영업시간 정보가 없습니다.</p>
            )}
          </div>
        </div>
        {d.latitude != null && d.longitude != null && (
          <div className="mt-3">
            <MapSection restaurants={[d]} />
          </div>
        )}
      </Section>
    </div>
  );
}

export default function DetailDialog({ restaurantId, name, onClose }: { restaurantId: number | null; name: string; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [fetched, setFetched] = useState<{ id: number; data?: RestaurantDetail; error?: string } | null>(null);

  // 열고 닫기
  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (restaurantId !== null && !dlg.open) dlg.showModal();
    if (restaurantId === null && dlg.open) dlg.close();
    document.body.style.overflow = restaurantId !== null ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [restaurantId]);

  // 상세 불러오기(이미 받은 식당은 캐시를 쓴다)
  useEffect(() => {
    if (restaurantId === null || cache.has(restaurantId)) return;
    const ac = new AbortController();
    fetchDetail(restaurantId, ac.signal)
      .then((data) => {
        cache.set(restaurantId, data);
        setFetched({ id: restaurantId, data });
      })
      .catch((e) => {
        if (ac.signal.aborted) return;
        setFetched({ id: restaurantId, error: e instanceof ApiError ? e.message : "식당 정보를 불러오지 못했어요." });
      });
    return () => ac.abort();
  }, [restaurantId]);

  const cached = restaurantId !== null ? cache.get(restaurantId) : undefined;
  const current = cached ? { data: cached, error: undefined } : fetched && fetched.id === restaurantId ? fetched : null;

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose(); // 바깥(어두운 배경) 클릭
      }}
      aria-label={`${name} 상세 정보`}
      className="m-auto max-h-[92vh] w-[min(94vw,760px)] overflow-y-auto overscroll-contain rounded-2xl bg-white p-0 text-neutral-900 shadow-2xl backdrop:bg-black/50"
    >
      {restaurantId !== null && (
        <div className="px-5 pb-8 sm:px-7">
          <div className="sticky top-0 z-20 -mx-5 flex items-center justify-between gap-3 bg-white px-5 pb-2 pt-5 sm:-mx-7 sm:px-7">
            <h2 className="text-xl font-extrabold">{name}</h2>
            <button type="button" onClick={onClose} aria-label="닫기" className="rounded-full px-3 py-1 text-lg text-neutral-500 hover:bg-neutral-100">
              ✕
            </button>
          </div>
          {!current && <p className="py-10 text-center text-sm text-neutral-500">불러오는 중…</p>}
          {current?.error && (
            <p role="alert" className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {current.error}
            </p>
          )}
          {current?.data && <Detail d={current.data} />}
        </div>
      )}
    </dialog>
  );
}
