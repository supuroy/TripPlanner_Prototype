/** Client mirror of ``WANDERLY_PARALLEL_POST_REFINE`` — early workspace apply + plan review without itinerary wait. */
export function parallelPostRefineEnabled(): boolean {
  return import.meta.env.VITE_WANDERLY_PARALLEL_POST_REFINE === "1";
}
