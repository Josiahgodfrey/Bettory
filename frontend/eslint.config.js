import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'src/lib/api-types.ts', 'playwright-report', 'test-results'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
    },
  },
  {
    // shadcn components and providers export helpers next to components (fast refresh hint only)
    files: ['src/components/ui/**', 'src/app/router.tsx', 'src/features/auth/auth-context.tsx', 'src/lib/theme.tsx', 'src/components/status.tsx', 'src/components/charts/chart-kit.tsx'],
    rules: { 'react-refresh/only-export-components': 'off' },
  },
)
