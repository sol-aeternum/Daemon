'use client';

import { useState } from 'react';

export default function Counter() {
  const [count, setCount] = useState(0);
  const [broken, setBroken] = useState(false);
  const renderPhase = 'healthy';
  if (broken && renderPhase === 'error')
    throw new Error('HMR_FIXTURE_RENDER_V1');
  return (
    <section>
      <p data-testid="client-version">client-v1</p>
      <button onClick={() => setCount((value) => value + 1)}>
        Count {count}
      </button>
      <button onClick={() => setBroken(true)}>Trigger render error</button>
    </section>
  );
}
