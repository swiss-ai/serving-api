const DEFAULT_API_URL = 'https://api.swissai.svc.cscs.ch';

export function getApiUrl(): string {
  if (typeof window !== 'undefined') {
    return (window as any).__API_URL__ || DEFAULT_API_URL;
  }
  return import.meta.env.VITE_API_URL || DEFAULT_API_URL;
}
