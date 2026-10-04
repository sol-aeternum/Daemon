import { proxySpeechCapabilities } from '../../../../lib/speechProxy';

export const runtime = 'nodejs';

export async function GET(req: Request) {
  return proxySpeechCapabilities(req);
}
