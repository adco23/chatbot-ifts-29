import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  {
    files: ['src/**/*.ts'],
    extends: [js.configs.recommended, ...tseslint.configs.strict],
    languageOptions: { globals: globals.browser },
    rules: { 'no-console': 'error' },
  },
);
