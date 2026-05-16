import type { HotelOption, HotelRankedPick } from "../components/TripStore";

/** Resolve the card fields shown for the user's current selection (default rank 1). */
export function resolveDisplayedHotel(hotel: HotelOption): HotelOption {
  const rank = hotel.selected_rank ?? 1;
  const picks = hotel.ranked_picks;
  if (!picks?.length) return { ...hotel, best: true };
  const pick = picks.find((p) => p.rank === rank) ?? picks[0];
  return {
    ...hotel,
    name: pick.name,
    area: pick.area,
    price: pick.price,
    image: pick.image ?? hotel.image,
    listing_url: pick.listing_url ?? hotel.listing_url,
    guest_rating: pick.guest_rating ?? hotel.guest_rating,
    review_count: pick.review_count ?? hotel.review_count,
    rating_label: pick.rating_label ?? hotel.rating_label,
    star_rating: pick.star_rating ?? hotel.star_rating,
    nights: hotel.nights,
    selected_rank: rank,
    ranked_picks: picks,
    best: true,
  };
}

/** Apply a new selection rank; keeps full ``ranked_picks`` for the dropdown. */
export function hotelWithSelectedRank(hotel: HotelOption, rank: number): HotelOption {
  const picks = hotel.ranked_picks;
  if (!picks?.length) return { ...hotel, selected_rank: rank, best: true };
  const pick = picks.find((p) => p.rank === rank);
  if (!pick) return { ...hotel, best: true };
  return {
    ...hotel,
    name: pick.name,
    area: pick.area,
    price: pick.price,
    image: pick.image ?? hotel.image,
    listing_url: pick.listing_url ?? hotel.listing_url,
    guest_rating: pick.guest_rating ?? hotel.guest_rating,
    review_count: pick.review_count ?? hotel.review_count,
    rating_label: pick.rating_label ?? hotel.rating_label,
    star_rating: pick.star_rating ?? hotel.star_rating,
    selected_rank: rank,
    ranked_picks: picks,
    best: true,
  };
}

export function otherRankedPicks(hotel: HotelOption): HotelRankedPick[] {
  const rank = hotel.selected_rank ?? 1;
  return (hotel.ranked_picks ?? []).filter((p) => p.rank !== rank);
}

export type HotelGuestRatingDisplay = {
  scoreText: string;
  detailText: string;
};

/** Guest score for hotel cards (Expedia 0–10 scale). */
export function hotelGuestRatingDisplay(hotel: HotelOption): HotelGuestRatingDisplay | null {
  if (hotel.guest_rating != null && Number.isFinite(hotel.guest_rating)) {
    const scoreText = `${Number(hotel.guest_rating).toFixed(1)}/10`;
    const label = (hotel.rating_label || "").trim();
    const rc = hotel.review_count;
    const tail: string[] = [];
    if (label) tail.push(label);
    if (typeof rc === "number" && rc >= 0) {
      tail.push(`${rc.toLocaleString()} review${rc === 1 ? "" : "s"}`);
    }
    return { scoreText, detailText: tail.join(" · ") };
  }
  const fallback = (hotel.star_rating || "").trim();
  if (!fallback) return null;
  return { scoreText: fallback, detailText: "" };
}
