// Test-only composition of actual production auth and resource components.
// Real PG-backed synthetic bearer credentials are installed via the public token
// interface after real coordination; no auth/fetch/cache module is substituted.
import { createRoot } from 'react-dom/client';
import { ResourceGallery } from '../components/ResourceGallery';
import { TaskResources } from '../components/ResourceCards';
import { normalizeDaemonMessages, getDaemonTaskId } from '../lib/chatMessages';
import { setAccessToken } from '../lib/auth';
import {
  readAuthCoordination,
  runAuthCookieTransaction,
} from '../lib/authCookieCoordinator';
import { ensureBrowserCacheCleared } from '../lib/browserCache';
import { resourceJSON } from '../lib/resources';
import { getAuthGeneration } from '../lib/auth';

const root = createRoot(document.getElementById('root')!);
declare global {
  interface Window {
    qualifyResourceUI(token: string, conversation: string): Promise<void>;
  }
}
window.qualifyResourceUI = async (token, conversation) => {
  await runAuthCookieTransaction(
    'sign-in',
    { epoch: readAuthCoordination()?.epoch ?? null, replaceIdentity: true },
    async (tx) => {
      tx.finish('stable');
    },
  );
  setAccessToken(token, Date.now() + 3600_000);
  await ensureBrowserCacheCleared({ reason: 'initialization' });
  const controller = new AbortController();
  const history = await resourceJSON<{ messages: unknown[] }>(
    `/conversations/${conversation}?pagination=cursor`,
    controller.signal,
    getAuthGeneration(),
  );
  const saved = normalizeDaemonMessages(history.messages).filter(
    (m) => m.role === 'assistant',
  );
  root.render(
    <>
      <ResourceGallery />
      <section aria-label="Saved chat turns">
        {saved.map((m) => (
          <section key={m.id}>
            <p>{m.content}</p>
            {getDaemonTaskId(m) && (
              <TaskResources taskId={getDaemonTaskId(m)!} />
            )}
          </section>
        ))}
      </section>
    </>,
  );
};
