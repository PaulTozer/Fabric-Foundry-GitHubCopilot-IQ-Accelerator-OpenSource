import typescript from '@typescript-eslint/eslint-plugin';
import parser from '@typescript-eslint/parser';
import react from 'eslint-plugin-react';
import hooks from 'eslint-plugin-react-hooks';
import accessibility from 'eslint-plugin-jsx-a11y';
import imports from 'eslint-plugin-import';

const config = [
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts', 'public/pdf.worker.min.mjs'] },
  {
    files: ['**/*.{js,mjs,cjs,ts,tsx}'],
    languageOptions: { parser, parserOptions: { ecmaFeatures: { jsx: true }, sourceType: 'module' } },
    plugins: { '@typescript-eslint': typescript, react, 'react-hooks': hooks, 'jsx-a11y': accessibility, import: imports },
    settings: { react: { version: 'detect' } },
    rules: {
      ...typescript.configs['eslint-recommended'].overrides[0].rules,
      ...typescript.configs.recommended.rules,
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      ...hooks.configs.recommended.rules,
      'react/no-unknown-property': 'off',
      'react/prop-types': 'off',
      'react/jsx-no-target-blank': 'off',
      'jsx-a11y/alt-text': ['error', { elements: ['img'], img: ['Image'] }],
      'jsx-a11y/aria-props': 'error',
      'jsx-a11y/aria-proptypes': 'error',
      'jsx-a11y/aria-unsupported-elements': 'error',
      'jsx-a11y/role-has-required-aria-props': 'error',
      'jsx-a11y/role-supports-aria-props': 'error',
      'import/no-anonymous-default-export': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
];
export default config;
