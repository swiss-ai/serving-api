const DEFAULT_API_URL = 'https://api.swissai.svc.cscs.ch';

export function getApiUrl(): string {
  if (typeof window !== 'undefined') {
    return (window as any).__API_URL__ || DEFAULT_API_URL;
  }
  // SSR uses the Docker service address; the browser keeps the public URL
  // injected by PageLayout. Runtime env also lets deployed images be reused.
  // import.meta.env covers a frontend started directly with astro dev.
  return process.env.INTERNAL_API_URL || process.env.VITE_API_URL || import.meta.env.VITE_API_URL || DEFAULT_API_URL;
}
