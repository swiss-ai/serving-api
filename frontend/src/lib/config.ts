const DEFAULT_API_URL = 'https://api.swissai.svc.cscs.ch';

export function getApiUrl(): string {
  if (typeof window !== 'undefined') {
    return (window as any).__API_URL__ || DEFAULT_API_URL;
  }
  // process.env wins so a deployed container can override at runtime (no
  // rebuild); import.meta.env covers `astro dev` / `make dummy-run` where
  // dotenv loads frontend/.env into Vite's env but NOT into process.env.
  return process.env.VITE_API_URL || import.meta.env.VITE_API_URL || DEFAULT_API_URL;
}
