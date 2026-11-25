'use client';

import { useEffect } from 'react';
import { api } from '@/lib/api';
import { applyRegionTheme } from '@/lib/region-theme';

export function RegionTheme() {
  useEffect(() => {
    api
      .getSettings()
      .then((settings) => {
        if (settings.target_region === 'europe' || settings.target_region === 'india') {
          applyRegionTheme(settings.target_region);
        }
      })
      .catch(() => {});
  }, []);
  return null;
}
