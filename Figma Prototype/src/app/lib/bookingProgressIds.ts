/** Session-scoped: which destination cards finished the live booking Playwright pipeline this page load. */
const bookingPipelineCompletedIds = new Set<string>();

export function isBookingPipelineCompletedFor(destId: string): boolean {
  return bookingPipelineCompletedIds.has(destId);
}

export function markBookingPipelineCompleted(destId: string): void {
  bookingPipelineCompletedIds.add(destId);
}

export function clearBookingPipelineCompletedFor(destId: string): void {
  bookingPipelineCompletedIds.delete(destId);
}

export function clearAllBookingPipelineCompleted(): void {
  bookingPipelineCompletedIds.clear();
}
