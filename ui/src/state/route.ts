// Minimal hash router: #/arena, #/inspector, #/bench, #/policy  (+ ?act=2&focus=m_17).
// Hash routes need no server rewrite rules, so FastAPI's StaticFiles(html=True) serves every view.
import { useCallback, useEffect, useState } from 'react';

export type View = 'arena' | 'inspector' | 'bench' | 'policy';
export const VIEWS: View[] = ['arena', 'inspector', 'bench', 'policy'];

export interface Route { view: View; params: URLSearchParams }

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '');
  const [path, query = ''] = raw.split('?');
  const view = (VIEWS as string[]).includes(path) ? (path as View) : 'arena';
  return { view, params: new URLSearchParams(query) };
}

export function buildHash(view: View, params: URLSearchParams): string {
  const q = params.toString();
  return `#/${view}${q ? `?${q}` : ''}`;
}

export function useRoute(): { route: Route; go: (view: View, patch?: Record<string, string | null>) => void } {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  const go = useCallback((view: View, patch?: Record<string, string | null>) => {
    const next = parseHash(window.location.hash);
    const params = new URLSearchParams(next.params);
    if (patch) for (const [k, v] of Object.entries(patch)) { if (v === null) params.delete(k); else params.set(k, v); }
    const hash = buildHash(view, params);
    if (hash !== window.location.hash) window.location.hash = hash;
  }, []);
  return { route, go };
}
