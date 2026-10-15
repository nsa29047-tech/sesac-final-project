import { BookOpen, ChefHat, Globe, MapPin, Sparkles, Star, type LucideIcon } from "lucide-react";

interface Example {
  q: string;
  label: string;
  icon: LucideIcon;
}

const EXAMPLES: Example[] = [
  { q: "서울에 있는 미슐랭 스타 식당 알려줘", label: "미슐랭", icon: Star },
  { q: "바삭하게 튀긴 음식이 맛있는 곳 추천해줘", label: "취향으로 찾기", icon: Sparkles },
  { q: "강남역 근처 일식집", label: "역·지역", icon: MapPin },
  { q: "파리에 있는 미슐랭 식당", label: "해외", icon: Globe },
  { q: "밍글스 메뉴랑 영업시간 알려줘", label: "식당 정보", icon: BookOpen },
  { q: "흑백요리사에 나온 셰프의 식당", label: "셰프", icon: ChefHat },
];

/** 첫 화면: 인사와 바로 눌러 볼 수 있는 예시 질문. */
export default function Welcome({ onPick }: { onPick: (q: string) => void }) {
  return (
    <section className="flex min-h-full flex-col justify-center py-10">
      <h2 className="text-[28px] font-extrabold leading-tight tracking-tight text-neutral-900 sm:text-[32px]">
        오늘은 어떤 맛집을
        <br />
        찾아 드릴까요?
      </h2>
      <p className="mt-3 text-[15px] text-neutral-500">
        미식 유튜브 영상에 나온 식당 약 120곳 가운데서 지역, 미슐랭, 메뉴, 분위기로 골라 드려요.
      </p>
      <div className="mt-8 grid gap-3 sm:grid-cols-2">
        {EXAMPLES.map(({ q, label, icon: Icon }) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className="group flex items-start gap-3 rounded-2xl border border-neutral-200 bg-white p-4 text-left transition hover:border-brand hover:bg-brand-soft"
          >
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-brand-soft text-brand group-hover:bg-white">
              <Icon size={18} aria-hidden />
            </span>
            <span>
              <span className="block text-xs font-semibold text-brand-ink">{label}</span>
              <span className="mt-0.5 block text-[14.5px] leading-snug text-neutral-800">{q}</span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}
