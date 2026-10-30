import { resourceProxy, resourceMethodDenied } from '@/lib/resourceProxy';
type Context = { params: Promise<{ path?: string[] }> };
export async function GET(request: Request, context: Context) {
  const { path = [] } = await context.params;
  return resourceProxy(
    request,
    `/resources${path.length ? `/${path.join('/')}` : ''}`,
  );
}
export const HEAD = GET;
export const POST = resourceMethodDenied;
export const PUT = resourceMethodDenied;
export const PATCH = resourceMethodDenied;
export const DELETE = resourceMethodDenied;
export const OPTIONS = resourceMethodDenied;
