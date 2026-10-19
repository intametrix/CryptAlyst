/**
 * Registers the TypeScript resolve hook used by `scripts/verify.mjs`.
 *
 *   node --import ./scripts/ts-resolve-register.mjs scripts/verify.mjs
 */
import { register } from 'node:module';

register('./ts-resolve.mjs', import.meta.url);
