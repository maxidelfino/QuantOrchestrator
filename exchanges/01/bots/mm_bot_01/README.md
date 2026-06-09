# mm_bot_01 runtime notes

## Compiled entrypoints

This package compiles into a nested output tree:

`dist/exchanges/01/bots/mm_bot_01/...`

Use the provided `start:*` scripts (already configured) after `npm run build`.

## tsx dev-mode limitation

`tsx` can fail to resolve some cross-directory imports that use `.js` specifiers and point outside this package directory (for example `shared/typescript/...`).

### Workaround

For reliable execution, run:

1. `npm run build`
2. One of the `start:*` scripts (compiled JS)

This keeps behavior consistent with TypeScript compilation and avoids resolver differences in `tsx` dev mode.
