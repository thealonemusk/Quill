export type AppRegion = 'india' | 'europe';

export function applyRegionTheme(region: AppRegion) {
  document.documentElement.dataset.region = region;
}
