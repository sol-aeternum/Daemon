import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  OPENUI487_FIXTURE,
  OPENUI487_RECORDED_LAYOUT,
  OPENUI487_SESSION_A,
} from '../__tests__/fixtures/openui487/fixture';
import { PILOT_LAYOUT_SCHEMA_VERSION } from '../__tests__/fixtures/openui487/layout';
import { Pilot } from '../__tests__/fixtures/openui487/Pilot';

function OfflineConversation() {
  const [final, setFinal] = useState(false);
  return (
    <div className="mx-auto max-w-3xl px-3 py-2">
      <p className="mb-2">
        Show the progress of this task, the files it produced, and a chart of
        these supplied figures.
      </p>
      <Pilot
        fixture={OPENUI487_FIXTURE}
        session={OPENUI487_SESSION_A}
        status={final ? 'completed' : 'running'}
        connection={final ? 'live' : 'reconnecting'}
        layoutEnabled
        layout={{
          frame: {
            schema_version: PILOT_LAYOUT_SCHEMA_VERSION,
            recorded_at: OPENUI487_FIXTURE.envelope.snapshot_at,
            text: OPENUI487_RECORDED_LAYOUT,
          },
          chunks: [
            final
              ? OPENUI487_RECORDED_LAYOUT
              : OPENUI487_RECORDED_LAYOUT.slice(0, 20),
          ],
          final,
        }}
      />
      <label className="mt-3 block">
        Message
        <textarea className="block w-full bg-bg-secondary p-2" />
      </label>
      <button
        type="button"
        onClick={() => {
          performance.mark('fixture-final-click');
          setFinal(true);
        }}
      >
        Finish fixture stream
      </button>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<OfflineConversation />);
