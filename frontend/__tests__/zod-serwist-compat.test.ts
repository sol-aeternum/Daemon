// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import { z as z3 } from 'zod/v3';
import { zodSchema } from '@ai-sdk/provider-utils';
import { fromZodError } from 'zod-validation-error/v4';
import {
  asyncFn,
  fn,
  injectManifestOptions as buildOptions,
} from '@serwist/build/schema';
import { injectManifestOptions as webpackOptions } from '@serwist/webpack-plugin/schema';
import { injectManifestOptions as nextOptions } from '@serwist/next/schema';

describe('Zod consumer integration', () => {
  it('converts and validates root-v4 and explicit-v3 schemas through the actual AI SDK', async () => {
    const rootV4 = z.object({ count: z.number().int() }).strict();
    const explicitV3 = z3.object({ count: z3.number().int() }).strict();
    expect(rootV4).toHaveProperty('_zod');
    expect(explicitV3).not.toHaveProperty('_zod');
    expect(z.toJSONSchema).toBeTypeOf('function');
    for (const schema of [rootV4, explicitV3]) {
      const adapter = zodSchema(schema);
      expect(adapter.jsonSchema).toMatchObject({
        type: 'object',
        required: ['count'],
        properties: { count: { type: 'integer' } },
        additionalProperties: false,
      });
      expect(await adapter.validate?.({ count: 2 })).toEqual({
        success: true,
        value: { count: 2 },
      });
      expect(await adapter.validate?.({ count: '2' })).toMatchObject({
        success: false,
      });
      expect(
        await adapter.validate?.({ count: 2, surprise: true }),
      ).toMatchObject({ success: false });
    }
  });

  it('preserves the real Next/webpack/build Serwist defaults and strictness', () => {
    const config = {
      swSrc: 'app/sw.ts',
      swDest: 'public/sw.js',
      register: true,
      disable: false,
    };
    expect(nextOptions.parse(config)).toMatchObject({
      ...config,
      swUrl: '/sw.js',
      cacheOnNavigation: false,
      reloadOnOnline: true,
      globPublicPatterns: ['**/*'],
    });
    expect(webpackOptions.parse({ swSrc: config.swSrc })).toMatchObject({
      swSrc: config.swSrc,
      injectionPoint: 'self.__SW_MANIFEST',
    });
    expect(
      buildOptions.parse({ ...configForBuild(), globDirectory: 'public' }),
    ).toMatchObject({
      ...configForBuild(),
      maximumFileSizeToCacheInBytes: 2097152,
      injectionPoint: 'self.__SW_MANIFEST',
    });
    expect(
      nextOptions.safeParse({ ...config, disablePrecacheManifest: true })
        .success,
    ).toBe(false);
    expect(nextOptions.safeParse({ ...config, register: 'yes' }).success).toBe(
      false,
    );
    expect(buildOptions.safeParse(configForBuild()).success).toBe(false);
    expect(
      webpackOptions.safeParse({ swSrc: 'app/sw.ts', unexpected: true })
        .success,
    ).toBe(false);
  });

  it('validates both sides of Serwist callback wrappers, including async output', async () => {
    const syncSchema = fn({ input: [z.string()], output: z.boolean() });
    const valid = syncSchema.parse((value: string) => value.length > 0);
    expect(valid('ok')).toBe(true);
    expect(() => Reflect.apply(valid, undefined, [12])).toThrow();
    const badOutput = syncSchema.parse(() => 'not a boolean');
    expect(() => badOutput('ok')).toThrow();
    const asyncSchema = asyncFn({ input: [z.string()], output: z.boolean() });
    const validAsync = asyncSchema.parse(async () => true);
    await expect(validAsync('ok')).resolves.toBe(true);
    const invalidAsync = asyncSchema.parse(async () => 'not a boolean');
    await expect(invalidAsync('ok')).rejects.toThrow();
  });

  it('formats the v4 error shape used by the React Hooks compiler validator', () => {
    const parsed = z
      .object({ enabled: z.boolean() })
      .safeParse({ enabled: 'yes' });
    expect(parsed.success).toBe(false);
    if (parsed.success)
      throw new Error('invalid synthetic fixture was accepted');
    const formatted = fromZodError(parsed.error);
    expect(formatted.details).toMatchObject([
      { code: 'invalid_type', path: ['enabled'], expected: 'boolean' },
    ]);
    expect(formatted.message).toContain('enabled');
  });
});

function configForBuild() {
  return { swSrc: 'app/sw.ts', swDest: 'public/sw.js' };
}
