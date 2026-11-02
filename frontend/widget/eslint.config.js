import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';
import jsxA11y from 'eslint-plugin-jsx-a11y';

export default tseslint.config(
  { ignores: ['dist', 'node_modules'] },
  {
    files: ['src/**/*.{ts,tsx}', 'vite.config.ts'],
    extends: [js.configs.recommended, ...tseslint.configs.strict, jsxA11y.flatConfigs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // RNF01: nada de logs con texto de estudiantes.
      'no-console': 'error',
    },
  },
  {
    // Loader que pega el docente en Moodle: JS plano, sin build.
    files: ['public/**/*.js'],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.browser, sourceType: 'script' },
    rules: { 'no-console': 'error' },
  },
);
