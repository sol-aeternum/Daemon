'use client';

import type { ButtonHTMLAttributes } from 'react';

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  'aria-label': string;
};

export function IconButton({
  type = 'button',
  className = '',
  ...props
}: IconButtonProps) {
  return (
    <button
      {...props}
      type={type}
      className={`inline-flex min-h-touch min-w-touch shrink-0 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-border-focus)] disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
    />
  );
}
