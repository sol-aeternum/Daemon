import { proxySpeechStream } from '../../../../../lib/speechProxy';

export const runtime = 'nodejs';

export async function POST(req: Request) {
  return proxySpeechStream(req);
}
