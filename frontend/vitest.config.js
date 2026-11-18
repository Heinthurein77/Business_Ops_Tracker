import { defineConfig } from 'vitest/config';

// Unit tests only (F-m6): the Playwright specs under e2e/ belong to
// `npm run test:e2e` and are excluded here so `npm test` never tries to
// run them under vitest.
export default defineConfig({
  test: {
    include: ['src/**/*.test.js'],
    environment: 'node',
  },
});
