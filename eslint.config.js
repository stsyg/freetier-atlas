import js from "@eslint/js";

export default [
  {
    ignores: ["node_modules/", ".venv/", "dist/", "build/", "coverage/", "apps/web/"],
  },
  js.configs.recommended,
];
