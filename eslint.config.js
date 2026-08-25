import js from "@eslint/js";

export default [
  {
    ignores: ["node_modules/", ".venv/", "dist/", "build/", "coverage/", "apps/web/"],
  },
  js.configs.recommended,
];

// Scratch control for the CI masking-edge demonstration. Delete with the branch.
// Never called, so loading this config still succeeds: the reference below is a
// STATIC no-undef finding, not a runtime ReferenceError that would crash ESLint
// before it could report anything.
function scratchCiControl() {
  return scratchUndefinedGlobal;
}
