import js from "@eslint/js";

export default [
  {
    ignores: ["node_modules/", ".venv/", "dist/", "build/", "coverage/"],
  },
  js.configs.recommended,
];
