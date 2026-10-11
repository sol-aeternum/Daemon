// Actual production provider/auth/cache modules, with fictional UI and tokens.
// This file is only bundled by the isolated test server, never a Next route.
// The fictional sign-in deliberately uses the real production identity helper
// (completeEmailSignIn -> coordinator -> actual Next proxy -> fixture upstream)
// instead of installing token state behind the coordinator's back.
import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import { AuthProvider, useAuth } from '../components/AuthProvider';
import { usePublicCredentialOwner } from '../components/AuthActionOwners';
import {
  clearLocalAuthState,
  completeEmailSignIn,
  ensureAuthHeader,
  getAccessToken,
  getAuthGeneration,
  getAuthHeader,
  getRefreshPromise,
  getSessionCoordinationProblem,
  hasValidAccessToken,
  logoutCurrentSession,
  refreshAccessToken,
} from '../lib/auth';
import {
  ensureBrowserCacheCleared,
  getBrowserCacheState,
} from '../lib/browserCache';
import { readAuthCoordination } from '../lib/authCookieCoordinator';
import { fetchAuthConfig } from '../lib/auth-config';

const OWNER_CODES = {
  'sign-in as A': 'fictional-owner-a',
  'sign-in as B': 'fictional-owner-b',
} as const;

// The public owner uses the production readiness/retirement hook. Fictional
// credentials alone replace AuthLanding's provider-specific identity UI.
function CredentialProbe() {
  const waitForReadiness = usePublicCredentialOwner();
  const [outcome, setOutcome] = useState('idle');
  useEffect(() => {
    const controller = new AbortController();
    window.__p02!.ownedSignIn = async (owner) => {
      const result = await window.__p02!.signIn(owner);
      if (result.success && (await waitForReadiness(result, controller.signal)))
        setOutcome('ready');
      return result;
    };
    return () => controller.abort();
  }, [waitForReadiness]);
  return <output data-testid="credential-readiness">{outcome}</output>;
}

function ProtectedProbe() {
  const { isAuthenticated, accessToken } = useAuth();
  const [content, setContent] = useState('No protected read');
  useEffect(() => {
    if (!isAuthenticated) return;
    let active = true;
    void (async () => {
      const header = await ensureAuthHeader();
      if (!active || !header) return;
      const response = await fetch(
        'http://127.0.0.1:3143/daemon/generated-files/fictional-account-output.txt',
        { headers: { Authorization: header } },
      );
      const value = await response.text();
      if (active) setContent(value);
    })();
    return () => {
      active = false;
    };
  }, [isAuthenticated, accessToken]);
  return <output data-testid="protected-cache-probe">{content}</output>;
}

function FixtureControls() {
  const [result, setResult] = useState('(idle)');

  const run = async (
    action: () => Promise<unknown>,
    label: string,
  ): Promise<void> => {
    try {
      const outcome = await action();
      setResult(`${label}:${JSON.stringify(outcome)}`);
    } catch (error) {
      setResult(`${label}:{"error":${JSON.stringify(String(error))}}`);
    }
  };

  return (
    <div id="fixture-controls">
      {Object.entries(OWNER_CODES).map(([buttonName, code]) => (
        <button
          key={code}
          data-testid={`identity-signin-${code}`}
          onClick={() =>
            run(
              () => completeEmailSignIn('fictional-challenge', code, 'private'),
              buttonName,
            )
          }
        >
          Fictional {buttonName}
        </button>
      ))}
      <button
        data-testid="fixture-rejection-attempt"
        onClick={() =>
          run(
            () =>
              completeEmailSignIn(
                'fictional-challenge',
                'fictional-expired-code',
                'private',
              ),
            'rejection-attempt',
          )
        }
      >
        Fictional rejection attempt
      </button>
      <button
        data-testid="fixture-local-exit"
        onClick={() => {
          clearLocalAuthState();
          setResult('local-exit:done');
        }}
      >
        Fictional local exit
      </button>
      <button
        data-testid="fixture-refresh"
        onClick={() => run(() => refreshAccessToken(), 'refresh')}
      >
        Fictional refresh
      </button>
      <button
        data-testid="fixture-cache-clear"
        onClick={() =>
          run(async () => {
            await ensureBrowserCacheCleared({ reason: 'revalidate' });
            return getBrowserCacheState();
          }, 'cache-clear')
        }
      >
        Fictional cache-clear driver
      </button>
      <output data-testid="fixture-result">{result}</output>
      <output data-testid="fixture-state">
        {JSON.stringify(window.__p02?.read())}
      </output>
    </div>
  );
}

interface WindowP02 {
  ready: boolean;
  signIn: (owner: 'a' | 'b') => ReturnType<typeof completeEmailSignIn>;
  ownedSignIn?: (owner: 'a' | 'b') => ReturnType<typeof completeEmailSignIn>;
  reject: () => ReturnType<typeof completeEmailSignIn>;
  refresh: () => ReturnType<typeof refreshAccessToken>;
  logout: () => ReturnType<typeof logoutCurrentSession>;
  exit: () => void;
  clearCache: () => ReturnType<typeof ensureBrowserCacheCleared>;
  read: () => {
    token: string | null;
    header: string | null;
    valid: boolean;
    problem: string | null;
    generation: number;
    cacheStatus: string;
    record: unknown | { unavailable: string };
    refreshPending: boolean;
  };
}
declare global {
  interface Window {
    __p02?: WindowP02;
  }
}

window.__p02 = {
  ready: false,
  signIn: (owner) =>
    completeEmailSignIn(
      'fictional-challenge',
      `fictional-owner-${owner}`,
      'private',
    ),
  reject: () =>
    completeEmailSignIn(
      'fictional-challenge',
      'fictional-expired-code',
      'private',
    ),
  refresh: () => refreshAccessToken(),
  logout: () => logoutCurrentSession(),
  exit: clearLocalAuthState,
  clearCache: () => ensureBrowserCacheCleared(),
  read: () => {
    let record: unknown;
    try {
      record = readAuthCoordination();
    } catch (error) {
      record = { unavailable: String(error) };
    }
    return {
      token: getAccessToken(),
      header: getAuthHeader(),
      valid: hasValidAccessToken(),
      problem: getSessionCoordinationProblem(),
      generation: getAuthGeneration(),
      cacheStatus: getBrowserCacheState().status,
      record,
      refreshPending: getRefreshPromise() !== null,
    };
  },
};

function FixtureRoot() {
  // React's passive mount traversal visits child effects before the parent.
  // A readiness acknowledgement here follows AuthProvider's initial effect,
  // not an arbitrary animation frame or a timer.
  useEffect(() => {
    window.__p02!.ready = true;
    return () => {
      window.__p02!.ready = false;
    };
  }, []);
  return (
    <>
      <AuthProvider>
        {new URLSearchParams(location.search).has('credential-owner') ? (
          <CredentialProbe />
        ) : null}
        <ProtectedProbe />
      </AuthProvider>
      <FixtureControls />
    </>
  );
}

async function startFixture() {
  if (new URLSearchParams(location.search).has('settled-auth-config')) {
    // Capability tests need a settled public-route mode, not the unrelated
    // mode-resolution redirect race. Fetch the REAL config before initial mount
    // so the provider initializes from that cache; no remount/module reset.
    const result = await fetchAuthConfig();
    if (result.status !== 'resolved')
      throw new Error('Fixture auth config did not resolve');
  }
  createRoot(document.getElementById('fixture-root')!).render(<FixtureRoot />);
}
void startFixture();
