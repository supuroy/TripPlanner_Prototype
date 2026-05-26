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
/** When false, ``cardStreamPush`` is ignored (card Q&A / composer captions only). */
let overlayArmed = false;

export function registerCardStreamOverlaySink(next: CardStreamOverlaySink | null): void {
  sink = next;
  if (!next) overlayArmed = false;
}

export function isCardStreamOverlayArmed(): boolean {
  return overlayArmed;
}

export function setCardStreamOverlayArmed(armed: boolean): void {
  overlayArmed = armed;
}

export function cardStreamOpen(opts: CardStreamOpenOpts): void {
  if (!sink || !opts.destId || opts.destId !== sink.destId) return;
  overlayArmed = true;
  sink.open(opts);
}

export function cardStreamPush(caption: string, destId?: string): void {
  const c = caption.trim();
  if (!overlayArmed || !c || !sink || !destId || destId !== sink.destId) return;
  sink.push(c);
}

/** Close when ``destId`` matches the registered card, or when omitted (single-card sink). */
export function cardStreamClose(delayMs = 1000, destId?: string): void {
  if (!sink) return;
  if (destId !== undefined && destId !== sink.destId) return;
  overlayArmed = false;
  sink.close(delayMs);
}

/** Wrap TripStore ``setAgentStreamCaption`` so captions also feed the overlay when registered. */
export function publishAgentStreamCaption(
  setCaption: (s: string) => void,
  caption: string,
): void {
  setCaption(caption);
  cardStreamPush(caption);
}
