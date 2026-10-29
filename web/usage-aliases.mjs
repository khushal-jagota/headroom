/** One resolver map keeps literal T3 imports identical in every browser host. */
export const usageAliases = {
  "@t3tools/contracts": new URL("./src/lib/usage/contracts/index.ts", import.meta.url).pathname,
  "@t3tools/shared/usageMerge": new URL("./src/lib/usage/usageMerge.ts", import.meta.url).pathname,
  "vite-plus/test": "vitest",
};
