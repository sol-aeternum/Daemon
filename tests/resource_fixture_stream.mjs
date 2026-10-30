// Test-host transport only; never used by the production resource proxy.
import { Readable } from 'node:stream';

export function pipeFixtureResponse(body, response, signal, failures, cancellations) {
  const source = Readable.fromWeb(body);
  source.on('error', (error) => {
    if (error.name === 'AbortError' && signal.aborted && response.destroyed) {
      cancellations.push('client_disconnect');
    } else {
      failures.push({ name: error.name, message: error.message });
    }
    if (!response.destroyed) response.destroy();
  });
  source.pipe(response);
  return source;
}
