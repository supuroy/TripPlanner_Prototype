/**
 * Lets ConversationPanel, CommandBar (via captions), and itinerary builds push text into the
 * expanded-card streaming overlay without importing React tree details.
 */

export type CardStreamOpenOpts = {
  title: string;
  subtitle?: string;
  /** When true, keep existing log text (e.g. chained operations). */
  preserveLog?: boolean;
  /** When set, only the expanded card with this id shows the overlay. */
  destId?: string;
};

type CardStreamOverlaySink = {
  destId: string;
  open: (opts: CardStreamOpenOpts) => void;
  push: (caption: string) => void;
  close: (delayMs?: number) => void;
};

let sink: CardStreamOverlaySink | null = null;

function targetsExpandedCard(destId?: string): boolean {
  if (!sink) return false;
  if (!destId) return true;
  return destId === sink.destId;
}

export function registerCardStreamOverlaySink(next: CardStreamOverlaySink | null): void {
  sink = next;
}

export function cardStreamOpen(opts: CardStreamOpenOpts): void {
  if (!targetsExpandedCard(opts.destId)) return;
  sink?.open(opts);
}

export function cardStreamPush(caption: string, destId?: string): void {
  const c = caption.trim();
  if (!c || !targetsExpandedCard(destId)) return;
  sink?.push(c);
}

export function cardStreamClose(delayMs = 1000, destId?: string): void {
  if (!targetsExpandedCard(destId)) return;
  sink?.close(delayMs);
}

/** Wrap TripStore ``setAgentStreamCaption`` so captions also feed the overlay when registered. */
export function publishAgentStreamCaption(
  setCaption: (s: string) => void,
  caption: string,
): void {
  setCaption(caption);
  cardStreamPush(caption);
}
