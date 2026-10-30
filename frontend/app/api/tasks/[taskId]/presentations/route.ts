import { resourceProxy, resourceMethodDenied } from '@/lib/resourceProxy';
export async function GET(
  request: Request,
  context: { params: Promise<{ taskId: string }> },
) {
  return resourceProxy(
    request,
    `/tasks/${(await context.params).taskId}/presentations`,
  );
}
export const HEAD = GET;
export const POST = resourceMethodDenied;
export const PUT = resourceMethodDenied;
export const PATCH = resourceMethodDenied;
export const DELETE = resourceMethodDenied;
export const OPTIONS = resourceMethodDenied;
