import type { DestCard, ProfilePill } from "../components/TripStore";



/** Mirror server ``is_outdoor_adventure_profile`` for UI gating. */

export function isOutdoorAdventureProfile(profile: ProfilePill[]): boolean {

  const style = profile.find((p) => p.key === "trip_style")?.value?.toLowerCase() ?? "";

  if (

    /\b(backpack|backpacking|multi[\s-]?day\s+hike|thru[\s-]?hike|wilderness\s+trek)\b/.test(style)

  ) {

    return true;

  }

  return /\b(car\s+camp|campground|camping|road\s+trip|trail)\b/.test(style);

}



/**

 * True when a destination is in the U.S. (NPS API scope).

 * Non-U.S. countries (e.g. Canada) return false even for hiking trips.

 */

export function isUsaDestinationCountry(country?: string | null): boolean {

  const c = (country || "").trim().toLowerCase();

  if (!c) return false;

  if (

    /\b(canada|mexico|british columbia|\bbc\b|ontario|quebec|alberta|manitoba|yukon)\b/.test(c)

  ) {

    return false;

  }

  return /\b(usa|u\.s\.a?\.?|united states|america)\b/.test(c);

}



/** Show the optional NPS enrich control on a destination card. */

export function canOfferNpsOutdoorEnrich(

  dest: Pick<DestCard, "country" | "outdoor_adventure" | "booking_hooks">,

  profile: ProfilePill[],

): boolean {

  const outdoor = dest.outdoor_adventure ?? dest.booking_hooks?.outdoor_adventure;

  if (!isOutdoorAdventureProfile(profile) && !outdoor) return false;

  return isUsaDestinationCountry(dest.country);

}


