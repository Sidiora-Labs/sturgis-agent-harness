import js from "@eslint/js";
import globals from "globals";

export default [
  {
    ignores: [
      "**/node_modules/**",
      "**/dist/**",
      "**/.astro/**",
      ".build/**",
      ".venv/**",
      ".codegraph/**",
      "consumer/web/**",
      "evidence/**",
      "native/memory/vendor/**",
      "documentation/coverage/**",
      ".research/9router-reference/**",
      ".data/**",
    ],
  },
  {
    files: ["**/*.{js,mjs,cjs}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      // Browser automation executes page callbacks inside Node-authored files.
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      ...js.configs.recommended.rules,
      "no-unused-vars": "off",
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
  },
];
