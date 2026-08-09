import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import LandingPage from '../app/landing/page';

describe('LandingPage', () => {
  it('routes every login and signup CTA through the canonical auth entry', () => {
    render(<LandingPage />);

    const authLinks = screen.getAllByRole('link');

    expect(authLinks).toHaveLength(4);
    expect(authLinks.map((link) => link.getAttribute('href'))).toEqual([
      '/auth',
      '/auth',
      '/auth',
      '/auth',
    ]);
  });
});
