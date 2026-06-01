/** Default zoom when opening a campground pin (street / trailhead scale). */
const CAMPGROUND_MAP_ZOOM = 15;

/**
 * Open Google Maps centered on exact coordinates (not a text search).
 * Uses the `@lat,lng,zoom` viewport form so Maps does not show a multi-result search list.
 */
export function googleMapsPinUrl(
  latitude: number | string | null | undefined,
  longitude: number | string | null | undefined,
  _label?: string | null,
): string | null {
  const lat = typeof latitude === "string" ? Number.parseFloat(latitude) : latitude;
  const lng = typeof longitude === "string" ? Number.parseFloat(longitude) : longitude;
  if (
    lat == null ||
    lng == null ||
    !Number.isFinite(lat) ||
    !Number.isFinite(lng) ||
    lat < -90 ||
    lat > 90 ||
    lng < -180 ||
    lng > 180
  ) {
    return null;
  }
  const coords = `${lat},${lng}`;
  return `https://www.google.com/maps/place/${coords}/@${coords},${CAMPGROUND_MAP_ZOOM}z`;
}
