"use client";

// 앱 안에서 영상을 재생하는 유튜브 플레이어와 메뉴 타임라인(유튜브 IFrame API).
// 타임라인의 메뉴를 누르면 그 시각으로 이동해 재생하고, 재생 중에는 현재 시각에 해당하는 메뉴를 강조한다.
// 플레이어는 스크롤해도 위에 고정된다(sticky). 시각은 자막에서 추정한 값이라 실제 화면과 몇 초 차이가 날 수 있다.
import { useEffect, useMemo, useRef, useState } from "react";
import { formatTime } from "@/lib/format";
import type { Menu } from "@/lib/types";

interface YTPlayer {
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  playVideo(): void;
  getCurrentTime(): number;
  destroy(): void;
}

declare global {
  interface Window {
    YT?: { Player: new (el: HTMLElement, opts: Record<string, unknown>) => YTPlayer };
    onYouTubeIframeAPIReady?: () => void;
  }
}

let apiPromise: Promise<void> | null = null;

/** 유튜브 IFrame API 스크립트를 한 번만 불러온다. */
function loadYouTubeApi(): Promise<void> {
  if (window.YT?.Player) return Promise.resolve();
  apiPromise ??= new Promise<void>((resolve, reject) => {
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      prev?.();
      resolve();
    };
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.onerror = () => {
      apiPromise = null;
      reject(new Error("youtube api"));
    };
    document.head.appendChild(script);
  });
  return apiPromise;
}

interface Item {
  sec: number | null;
  name: string;
  stage: string | null;
  desc: string | null;
}

export default function VideoPlayer({ videoId, url, menus }: { videoId: string; url: string; menus: Menu[] }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<YTPlayer | null>(null);
  const [failed, setFailed] = useState(false);
  const [current, setCurrent] = useState(-1);

  // 시각이 있는 음식은 시각순, 시각이 없는 음식은 뒤에(원래 순서)
  const items: Item[] = useMemo(
    () =>
      menus
        .filter((m) => m.item_type === "FOOD")
        .sort(
          (a, b) =>
            Number(a.mentioned_sec === null) - Number(b.mentioned_sec === null) ||
            (a.mentioned_sec ?? 0) - (b.mentioned_sec ?? 0) ||
            (a.menu_order ?? 0) - (b.menu_order ?? 0),
        )
        .map((m) => ({ sec: m.mentioned_sec, name: m.name, stage: m.course_stage, desc: m.description })),
    [menus],
  );
  const itemsRef = useRef(items);
  useEffect(() => {
    itemsRef.current = items; // 타이머가 항상 최신 목록을 보도록(렌더링 중에는 ref 를 쓰지 않는다)
  }, [items]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let player: YTPlayer | null = null;
    loadYouTubeApi()
      .then(() => {
        if (cancelled || !hostRef.current || !window.YT) return;
        const mount = document.createElement("div"); // YT.Player 가 이 요소를 iframe 으로 바꾼다(React 가 관리하는 노드와 분리)
        hostRef.current.appendChild(mount);
        player = new window.YT.Player(mount, {
          videoId,
          width: "100%",
          height: "100%",
          playerVars: { rel: 0, modestbranding: 1, playsinline: 1 },
          events: { onError: () => setFailed(true) },
        });
        playerRef.current = player;
        timer = window.setInterval(() => {
          const p = playerRef.current;
          if (!p || typeof p.getCurrentTime !== "function") return;
          const t = p.getCurrentTime();
          let cur = -1;
          itemsRef.current.forEach((it, i) => {
            if (it.sec !== null && it.sec <= t) cur = i;
          });
          setCurrent(cur);
        }, 500);
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      try {
        player?.destroy();
      } catch {}
      playerRef.current = null;
    };
  }, [videoId]);

  function seek(sec: number) {
    const p = playerRef.current;
    if (!p || typeof p.seekTo !== "function") return;
    p.seekTo(sec, true);
    p.playVideo();
  }

  return (
    <div>
      <div className="sticky top-16 z-10 bg-white pb-2">
        <div className="relative aspect-video w-full overflow-hidden rounded-xl bg-black">
          <div ref={hostRef} className="absolute inset-0 [&_iframe]:h-full [&_iframe]:w-full" />
          {failed && (
            <p className="absolute inset-0 flex items-center justify-center p-4 text-center text-sm leading-relaxed text-white">
              <span>
                이 영상은 앱 안에서 재생할 수 없어요.{" "}
                <a href={url} target="_blank" rel="noreferrer" className="text-[#ffb199] underline">
                  YouTube에서 보기
                </a>
              </span>
            </p>
          )}
        </div>
      </div>

      <ol className="mt-1 border-l-2 border-brand-line">
        {items.map((it, i) => {
          const body = (
            <>
              <span
                className={
                  it.sec !== null
                    ? "inline-block rounded-full bg-brand px-2.5 py-0.5 text-[12.5px] font-bold text-white"
                    : "inline-block rounded-full bg-neutral-200 px-2.5 py-0.5 text-[12.5px] font-medium text-neutral-500"
                }
              >
                {it.sec !== null ? `▶ ${formatTime(it.sec)}` : "시각 정보 없음"}
              </span>
              <span className="mt-1.5 block text-[15px] font-bold text-neutral-900">
                {it.name}
                {it.stage && <span className="ml-1.5 rounded-full bg-brand-soft px-2 py-px text-[11px] font-medium text-[#a66]">{it.stage}</span>}
              </span>
              {it.desc && <span className="mt-0.5 block text-[13.5px] leading-relaxed text-neutral-600">{it.desc}</span>}
            </>
          );
          const base = `relative block w-full rounded-r-[10px] py-2 pl-[18px] pr-2.5 text-left before:absolute before:left-[-6px] before:top-3.5 before:h-2.5 before:w-2.5 before:rounded-full before:border-2 before:border-white before:bg-brand ${
            i === current ? "bg-[#fff1ec] before:shadow-[0_0_0_4px_#ffd2c5]" : ""
          }`;
          return (
            <li key={`${it.name}-${i}`}>
              {it.sec !== null ? (
                <button type="button" onClick={() => seek(it.sec as number)} className={`${base} cursor-pointer hover:bg-[#fff7f4]`}>
                  {body}
                </button>
              ) : (
                <div className={base}>{body}</div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
