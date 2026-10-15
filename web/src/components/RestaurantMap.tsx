"use client";

// 추천 식당을 번호 마커로 찍는 지도(Leaflet + OpenStreetMap 타일, API 키 불필요). 브라우저에서만 동작하므로 MapSection 이 ssr:false 로 불러온다.
import { useEffect, useMemo } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MapContainer, Marker, Popup, TileLayer, useMap } from "react-leaflet";
import type { Restaurant } from "@/lib/types";
import { michelinBadge, shortRegion } from "@/lib/format";

interface Pin {
  r: Restaurant;
  n: number | null; // 카드 번호. null 이면 카드로 소개하지 않은 식당(회색 점)
  pos: [number, number];
}

function pinIcon(n: number | null, single: boolean) {
  if (n === null) {
    return L.divIcon({ className: "", html: `<div class="map-pin-dim"></div>`, iconSize: [16, 16], iconAnchor: [8, 8], popupAnchor: [0, -8] });
  }
  return L.divIcon({
    className: "",
    html: `<div class="map-pin${single ? " map-pin-one" : ""}">${single ? "●" : n}</div>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
    popupAnchor: [0, -14],
  });
}

/** 마커가 모두 보이도록 범위를 맞춘다. 한 곳이면 그곳을 중심으로 확대한다. 컨테이너 크기가 바뀌면(결과 패널이 열리거나 창 크기가 바뀔 때) 다시 맞춘다. */
function FitBounds({ pins }: { pins: Pin[] }) {
  const map = useMap();
  useEffect(() => {
    const fit = () => {
      map.invalidateSize();
      if (pins.length === 1) map.setView(pins[0].pos, 15);
      else map.fitBounds(L.latLngBounds(pins.map((p) => p.pos)), { padding: [30, 30], maxZoom: 16 });
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(map.getContainer());
    return () => observer.disconnect();
  }, [map, pins]);
  return null;
}

export default function RestaurantMap({ restaurants, extra = [], onDetail }: { restaurants: Restaurant[]; extra?: Restaurant[]; onDetail?: (r: Restaurant) => void }) {
  // 번호는 카드 번호와 같아야 하므로 좌표가 없는 식당이 있어도 원래 순서를 유지한다
  // restaurants 가 그대로면 pins 도 그대로여야 한다(새로 만들면 채팅이 갱신될 때마다 지도가 다시 맞춰져 사용자가 옮긴 위치가 풀린다)
  // extra 는 카드로 소개하지 않았지만 조건에 맞는 식당이다. 번호 없이 회색 점으로 함께 찍어 전체 분포를 보여 준다
  const pins: Pin[] = useMemo(() => {
    const at = (r: Restaurant) => (r.latitude != null && r.longitude != null ? ([r.latitude, r.longitude] as [number, number]) : null);
    const numbered = restaurants.flatMap((r, i) => {
      const pos = at(r);
      return pos ? [{ r, n: i + 1, pos }] : [];
    });
    const dim = extra.flatMap((r) => {
      const pos = at(r);
      return pos ? [{ r, n: null, pos }] : [];
    });
    return [...dim, ...numbered]; // 번호 마커가 회색 점 위에 그려지도록 뒤에 둔다
  }, [restaurants, extra]);
  if (pins.length === 0) return null;
  const numberedCount = pins.filter((p) => p.n !== null).length;

  return (
    <MapContainer center={pins[0].pos} zoom={13} scrollWheelZoom={false} className="h-80 w-full rounded-2xl">
      <TileLayer attribution="&copy; OpenStreetMap" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" maxZoom={19} />
      <FitBounds pins={pins} />
      {pins.map(({ r, n, pos }) => (
        <Marker key={r.restaurant_id} position={pos} icon={pinIcon(n, numberedCount === 1 && pins.length === 1)} zIndexOffset={n === null ? -1000 : 0}>
          <Popup>
            <div className="min-w-[170px] leading-normal">
              <b className="text-sm text-neutral-900">
                {n !== null && pins.length > 1 ? `${n}. ` : ""}
                {r.name}
              </b>
              {shortRegion(r.region) && <div className="text-xs text-neutral-500">{shortRegion(r.region)}</div>}
              {r.michelin && <div className="text-xs font-bold text-red-700">{michelinBadge(r.michelin.label)}</div>}
              {r.rating != null && <div className="text-[12.5px] font-bold text-brand">★ {r.rating}</div>}
              <div className="mt-1.5 flex gap-1.5">
                {r.videos[0] && (
                  <a href={r.videos[0].url} target="_blank" rel="noreferrer" className="map-popup-link">
                    영상 보기
                  </a>
                )}
                {r.google_maps_url && (
                  <a href={r.google_maps_url} target="_blank" rel="noreferrer" className="map-popup-link">
                    지도
                  </a>
                )}
              </div>
              {onDetail && (
                <button type="button" onClick={() => onDetail(r)} className="mt-2 w-full rounded-full bg-brand py-1.5 text-xs font-bold text-white hover:bg-brand-dark">
                  상세 정보 보기
                </button>
              )}
            </div>
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  );
}
