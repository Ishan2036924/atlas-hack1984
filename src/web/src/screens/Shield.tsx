// Settings › Atlas Shield (D125): what the AI can see. Four levels (Standard by default; Private, Strict, Custom with
// its groups), pictures (Shield can't look inside one: they go to the AI as they are, or stay with you), how it works
// and its honest limits, and a way to the always-visible list (it lives on About me, next to the people it is about).
// Each choice saves at once through PATCH /v1/shield; the screen moves first and steps back if the server says no. A
// change counts from the next message. With Open Pseudonym on for this account, a row opens What the AI saw (AiSaw.tsx).
import { useQuery } from "@tanstack/react-query";

import type { Api } from "../lib/api";
import { useAssistantName } from "../lib/assistant";
import { GROUPS, HOW_IT_WORKS, hiddenGroups, isOn, LEVELS, LIMITS, NAMES_FOUND, namesHidden, PICTURES, picturesOf, type ShieldGroup, type ShieldLevel } from "../lib/shield";
import { useShield } from "../lib/useShield";
import { Switch } from "../ui/controls";
import { Icon, Section } from "../ui/kit";
import { RowButton } from "../ui/primitives";

export function ShieldSettings({ api, onAbout, onSaw }: { api: Api; onAbout?: () => void; onSaw?: () => void }) {
  const { state, save } = useShield(api);
  const pseudonym = useQuery({ queryKey: ["pseudonym"], queryFn: api.pseudonym });
  const assistant = useAssistantName();
  const s = state.data;
  if (state.isError) return <p className="m-0 px-1 text-[14px] text-bad">Couldn't load Atlas Shield. Try again in a moment.</p>;
  if (!isOn(s)) return null;
  const hidden = hiddenGroups(s);
  const seesPictures = picturesOf(s) === "send";
  const pick =(level: ShieldLevel) => save.mutate(level === "custom" && s.level !== "custom" ? { level, groups: hiddenGroups(s) } : { level });
  const toggle = (g: ShieldGroup, on: boolean) => save.mutate({ groups: GROUPS.map((x) => x.id).filter((x) => (x === g ? on : s.groups.includes(x))) });
  return (
    <>
      <Section title="What the AI can see">
        <div role="radiogroup" aria-label="Shield level" className="divide-y divide-line">
          {LEVELS.map((l) => {
            const chosen = s.level === l.id;
            return (
              <button key={l.id} type="button" role="radio" aria-checked={chosen} onClick={() => !chosen && pick(l.id)} className="flex min-h-16 w-full items-start gap-3 px-4 py-3.5 text-left">
                <span aria-hidden className={`mt-0.5 flex size-[22px] shrink-0 items-center justify-center rounded-full border-2 ${chosen ? "border-ink bg-ink text-page" : "border-line"}`}>
                  {chosen && <Icon name="check" size={13} stroke={3} />}
                </span>
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-[17px] font-medium">
                    {l.label}
                    {l.id === "standard" && <span className="font-normal text-ink-2"> (default)</span>}
                  </span>
                  <span className="text-[14px] leading-snug text-ink-2">{l.line.replace("Atlas", assistant)}</span>
                </span>
              </button>
            );
          })}
        </div>
      </Section>

      {s.level === "custom" && (
        <Section title="Hide too">
          {GROUPS.map((g) => (
            <div key={g.id} className="flex min-h-13 items-center justify-between gap-4 px-4 py-2.5">
              <span className="text-[16px]">{g.label}</span>
              <Switch on={hidden.includes(g.id)} label={`Hide ${g.label.toLowerCase()}`} onChange={(on) => toggle(g.id, on)} />
            </div>
          ))}
          <p className="m-0 px-4 py-3 text-[14px] leading-snug text-ink-2">Cards, bank numbers, SSNs, passwords, keys and codes are always hidden.</p>
        </Section>
      )}

      <Section title="Pictures">
        <div className="flex min-h-16 items-center justify-between gap-4 px-4 py-3">
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="text-[16px]">{PICTURES.label}</span>
            <span className="text-[14px] leading-snug text-pretty text-ink-2">{seesPictures ? PICTURES.send : PICTURES.keep}</span>
          </span>
          <Switch on={seesPictures} label={PICTURES.label} onChange={(on) => save.mutate({ pictures: on ? "send" : "keep" })} />
        </div>
      </Section>

      <Section title="Always visible">
        <button type="button" onClick={onAbout} disabled={!onAbout} className="flex min-h-14 w-full items-center gap-3 px-4 py-2.5 text-left">
          <span className="shrink-0 text-ink-2">
            <Icon name="shield" size={20} />
          </span>
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-[16px]">People and details</span>
            <span className="truncate text-[14px] text-ink-2">{s.visible.length ? `${s.visible.length} on the list. Edit it in About me` : "None yet. Add them in About me"}</span>
          </span>
          <span className="shrink-0 text-ink-3">
            <Icon name="chevron" size={16} />
          </span>
        </button>
      </Section>

      {pseudonym.data?.enabled && onSaw && (
        <Section title="What the AI saw">
          <RowButton onClick={onSaw} className="min-h-14 items-center">
            <span aria-hidden className="shrink-0 text-fg-2">
              <Icon name="list" size={20} />
            </span>
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="text-callout text-fg-1">Your recent messages</span>
              <span className="text-body text-fg-2">Check what the AI got, message by message</span>
            </span>
            <span aria-hidden className="shrink-0 text-fg-3">
              <Icon name="chevron" size={16} />
            </span>
          </RowButton>
        </Section>
      )}

      <Section title="How it works">
        <div className="flex flex-col gap-3 px-4 py-3.5 text-[15px] leading-relaxed">
          <p className="m-0">{HOW_IT_WORKS.replace("Atlas", assistant)}</p>
          {namesHidden(s) && s.names === "cues" && <p className="m-0 text-ink-2">{NAMES_FOUND.replaceAll("{assistant}", assistant)}</p>}
          <p className="m-0 text-ink-2">{LIMITS}</p>
        </div>
      </Section>
      {save.isError && <p role="alert" className="m-0 px-1 text-[14px] text-bad">Couldn't save that. Try again in a moment.</p>}
    </>
  );
}
