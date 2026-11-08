// Settings › Atlas Shield › What the AI saw (Open Pseudonym, lib/pseudonym.ts): your recent messages as the AI got
// them. The surface is one list: where it came from, when, and what was hidden in your words. One tap opens a message:
// your words above what the AI got (the same stand-ins marked, so the list's line and the marks always agree), and
// whether every request's signed receipt checked out; one tap further down, each request (time, model, what it carried
// with the chat so far, its fingerprint). A receipt that doesn't hold up opens that fold and says so plainly. Nothing
// here changes anything.
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

import type { Api } from "../lib/api";
import { useWideLayout } from "../lib/layout";
import { hiddenLine, jobLabel, kindsList, SAW_LINE, segments, shortHash, STAND_IN_NOTE, type Turn, type TurnRow, when, whyWords, wordsOf } from "../lib/pseudonym";
import { useLoading } from "../lib/useLoading";
import { clock } from "../lib/view";
import { Fold } from "../ui/Fold";
import { Icon, Section } from "../ui/kit";
import { Drawer } from "../ui/overlay";
import { Button, EmptyState, IconButton, RowButton } from "../ui/primitives";
import { SkeletonRows } from "../ui/Skeleton";

export function AiSawSettings({ api, now }: { api: Api; now?: Date }) {
  const q = useInfiniteQuery({
    queryKey: ["pseudonym", "turns"],
    queryFn: ({ pageParam }) => api.pseudonymTurns(pageParam || undefined),
    initialPageParam: "",
    getNextPageParam: (last) => (last.more && last.turns.length ? last.turns[last.turns.length - 1].at : undefined),
  });
  const [open, setOpen] = useState<TurnRow | null>(null);
  const loading = useLoading(q.isPending);
  const rows = q.data?.pages.flatMap((p) => p.turns) ?? [];
  let body;
  if (q.isError)
    body = (
      <div className="flex flex-col items-start gap-3 px-4 py-3.5">
        <p className="m-0 text-body text-fg-2">Couldn't load what the AI saw. Check your connection and try again.</p>
        <Button variant="secondary" size="sm" onClick={() => void q.refetch()}>
          Try again
        </Button>
      </div>
    );
  else if (q.isPending) body = loading ? <SkeletonRows rows={4} label="Loading what the AI saw" /> : null;
  else if (!rows.length)
    body = <EmptyState icon="shield" compact title="Nothing here yet" hint="After your next message, you'll see here exactly what the AI got in its place." />;
  else
    body = rows.map((r) => (
      <RowButton key={r.id} onClick={() => setOpen(r)} className="items-center">
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="text-callout text-fg-1">
            {jobLabel(r.job)}
            <span className="text-fg-2">, {when(r.at, now)}</span>
          </span>
          <span className="text-body text-fg-2">{hiddenLine(r.words ?? {}, "Nothing private in your words")}</span>
        </span>
        <span aria-hidden className="shrink-0 text-fg-3">
          <Icon name="chevron" size={16} />
        </span>
      </RowButton>
    ));
  return (
    <>
      <p className="m-0 px-1 text-body text-pretty text-fg-2">{SAW_LINE}</p>
      <Section title="Recent messages" quiet>
        {body}
      </Section>
      {q.hasNextPage && (
        <div className="px-1">
          <Button variant="secondary" size="sm" loading={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>
            Show earlier messages
          </Button>
        </div>
      )}
      <TurnSheet api={api} row={open} now={now} onClose={() => setOpen(null)} />
    </>
  );
}

function TurnSheet({ api, row, now, onClose }: { api: Api; row: TurnRow | null; now?: Date; onClose: () => void }) {
  const wide = useWideLayout();
  const id = row?.id ?? "";
  const q = useQuery({ queryKey: ["pseudonym", "turn", id], queryFn: () => api.pseudonymTurn(id), enabled: Boolean(id) });
  const loading = useLoading(q.isPending && Boolean(id));
  const title = row ? `${jobLabel(row.job)}, ${when(row.at, now)}` : "What the AI saw";
  return (
    <Drawer open={row !== null} onOpenChange={(o) => !o && onClose()} title={title} side={wide ? "right" : "bottom"} detents={[0.92]} showTitle={false}>
      <div className="flex flex-col gap-5">
        <div className={`flex items-center justify-between gap-3 ${wide ? "pt-3" : ""}`}>
          <h2 className="m-0 min-w-0 text-callout font-semibold text-fg-1">{title}</h2>
          <IconButton icon="close" label="Close" onClick={onClose} />
        </div>
        {q.isError ? (
          <p className="m-0 text-body text-fg-2">Couldn't open this message. Close it and try again.</p>
        ) : q.data ? (
          <TurnView turn={q.data} />
        ) : loading ? (
          <SkeletonRows rows={3} label="Opening the message" />
        ) : null}
      </div>
    </Drawer>
  );
}

export function TurnView({ turn }: { turn: Turn }) {
  const n = turn.calls.length;
  const bad = turn.calls.filter((c) => !c.ok);
  const words = turn.words ?? wordsOf(turn.seen, turn.marks);
  return (
    <div className="flex flex-col gap-6">
      <Words label="You wrote">{turn.real ?? <span className="text-fg-2">Your words can't be shown: Atlas's key has changed since.</span>}</Words>
      <Words label="The AI got" note={turn.marks.length ? STAND_IN_NOTE : undefined}>
        {turn.seen === null ? (
          <span className="text-fg-2">Not kept for this message.</span>
        ) : (
          segments(turn.seen, turn.marks).map((s, i) =>
            s.kind ? (
              <mark key={i} className="rounded-badge bg-info-soft px-0.5 text-fg-1">
                {s.text}
              </mark>
            ) : (
              <span key={i}>{s.text}</span>
            ),
          )
        )}
      </Words>
      <div className="flex items-start gap-2.5">
        <span aria-hidden className={`mt-0.5 shrink-0 ${bad.length ? "text-status-warn-ink" : "text-status-live-ink"}`}>
          <Icon name={bad.length ? "info" : "check"} size={18} />
        </span>
        <p className="m-0 text-body text-pretty text-fg-1">
          {bad.length
            ? `${bad.length === 1 ? "One receipt doesn't" : `${bad.length} receipts don't`} match what Atlas signed when this message was sent, so ${bad.length === 1 ? "it" : "they"} can't be trusted. If you see this again, tell us in Feedback.`
            : `${hiddenLine(words, "Nothing private")} in your words. ${n === 1 ? "Its request has a signed receipt, and it checks out." : `Each of its ${n} requests has a signed receipt, and all check out.`}`}
        </p>
      </div>
      <Section title="Each request" quiet>
        <Fold label="Each request" startOpen={bad.length > 0}>
          <ol className="m-0 flex list-none flex-col gap-4 p-0">
            {turn.calls.map((c) => (
              <li key={c.seq} className="flex items-start gap-2.5 text-body">
                <span aria-hidden className={`mt-0.5 shrink-0 ${c.ok ? "text-status-live-ink" : "text-status-warn-ink"}`}>
                  <Icon name={c.ok ? "check" : "info"} size={16} />
                </span>
                <span className="flex min-w-0 flex-col gap-0.5">
                  <span className="text-fg-1">
                    Request {c.seq}, {clock(c.at)}, <span className="whitespace-nowrap">{c.model}</span>
                  </span>
                  <span className="text-fg-2">{kindsList(c.stand_ins) ? `Hidden with the chat so far: ${kindsList(c.stand_ins)}` : "Nothing private in it"}</span>
                  {!c.ok && <span className="text-status-warn-ink">{whyWords(c.why)}</span>}
                  <span className="text-caption text-fg-2 tabular-nums">Fingerprint {shortHash(c.payload)}</span>
                </span>
              </li>
            ))}
          </ol>
        </Fold>
      </Section>
    </div>
  );
}

function Words({ label, note, children }: { label: string; note?: string; children: ReactNode }) {
  return (
    <figure className="m-0 flex min-w-0 flex-col gap-2">
      <figcaption className="flex flex-col gap-0.5">
        <span className="text-caption font-semibold text-fg-2">{label}</span>
        {note && <span className="text-caption text-pretty text-fg-2">{note}</span>}
      </figcaption>
      <p className="m-0 rounded-tile bg-surface-field px-3.5 py-3 text-callout whitespace-pre-wrap text-fg-1 wrap-anywhere">{children}</p>
    </figure>
  );
}
