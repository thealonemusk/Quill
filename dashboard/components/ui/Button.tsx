import clsx from 'clsx';
import { ButtonHTMLAttributes } from 'react';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: 'sm' | 'md';
}

const variants: Record<Variant, string> = {
  primary:
    'bg-signal shadow-[var(--shadow-signal)] hover:brightness-110 disabled:opacity-50 disabled:shadow-none',
  secondary:
    'bg-[var(--surface)] text-[var(--text-primary)] border border-[var(--border)] shadow-[var(--shadow-rest)] hover:bg-[var(--surface-2)]',
  ghost:
    'text-[var(--text-secondary)] hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]',
  danger:
    'bg-[var(--danger-bg)] text-[var(--danger)] border border-[var(--danger-border)] hover:brightness-110',
};

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  children,
  ...props
}: ButtonProps) {
  return (
    <button
      className={clsx(
        'inline-flex items-center justify-center gap-2 rounded-xl font-semibold tracking-tight transition-all duration-150 active:scale-[0.98]',
        size === 'sm' ? 'px-3 py-2 text-sm sm:py-1.5' : 'px-4 py-2.5 text-sm sm:py-2',
        variants[variant],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
