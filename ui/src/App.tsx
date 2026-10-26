import { AlertTriangle, RefreshCw } from 'lucide-react';
import { UiProvider } from './state/ui';
import { AppProvider, useData } from './state/app';
import { useRoute } from './state/route';
import { useShortcuts } from './state/shortcuts';
import { Header, TimelineBar } from './components/Shell';
import { ShortcutHelp, Toasts } from './components/Overlays';
import { Button } from './components/ui';
import { ArenaView } from './views/Arena/ArenaView';
import { InspectorView } from './views/Inspector/InspectorView';
import { BenchView } from './views/Bench/BenchView';
import { PolicyView } from './views/Policy/PolicyView';

function Body() {
  const { route } = useRoute();
  const { trace, loading, error, retryBackend } = useData();
  const needsTrace = route.view === 'arena' || route.view === 'inspector';

  if (needsTrace && !trace) {
    return (
      <div className="grid h-full place-items-center p-8">
        <div className="card max-w-xl p-8 text-center">
          {error ? (
            <>
              <AlertTriangle size={36} className="mx-auto text-warn" />
              <h2 className="mt-3 text-2xl font-bold">Could not load a trace</h2>
              <p className="mt-2 text-muted">{error}</p>
              <Button className="mt-5" icon={RefreshCw} onClick={() => void retryBackend()}>Try again</Button>
            </>
          ) : (
            <>
              <div className="mx-auto h-9 w-9 animate-spin rounded-full border-4 border-border border-t-ok" />
              <h2 className="mt-4 text-xl font-semibold">{loading ? 'Loading the trace…' : 'Waiting for a trace'}</h2>
            </>
          )}
        </div>
      </div>
    );
  }
  switch (route.view) {
    case 'arena': return <ArenaView />;
    case 'inspector': return <InspectorView />;
    case 'bench': return <BenchView />;
    case 'policy': return <PolicyView />;
  }
}

function Frame() {
  useShortcuts();
  const { route } = useRoute();
  const showTimeline = route.view === 'arena' || route.view === 'inspector';
  return (
    <div className="flex h-full min-w-[1100px] flex-col">
      <Header />
      {showTimeline && <TimelineBar />}
      <main className="relative min-h-0 flex-1 overflow-hidden">
        <Body />
      </main>
      <Toasts />
      <ShortcutHelp />
    </div>
  );
}

export default function App() {
  return (
    <UiProvider>
      <AppProvider>
        <Frame />
      </AppProvider>
    </UiProvider>
  );
}
