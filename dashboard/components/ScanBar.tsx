'use client';

import { RefreshCw, EyeOff, Trash2, Send, Square } from 'lucide-react';
import clsx from 'clsx';
import { Button } from '@/components/ui/Button';

interface ScanBarProps {
  scanning: boolean;
  applying?: boolean;
  onScan: () => void;
  onRemoveVisited: () => void;
  onClear: () => void;
  onApplyAll?: () => void;
  onStopApply?: () => void;
  onStopScan?: () => void;
}

export function ScanBar({
  scanning,
  applying,
  onScan,
  onRemoveVisited,
  onClear,
  onApplyAll,
  onStopApply,
  onStopScan,
}: ScanBarProps) {
  return (
    // Wraps instead of overflowing, and right-aligned so the primary action
    // stays nearest the thumb when the row breaks onto a second line.
    // Below `sm` the secondary buttons keep their icon and drop the label —
    // four labelled buttons do not fit a 390px screen, and an icon with an
    // aria-label reads better than a squashed row.
    <div className="flex w-full min-w-0 max-w-full basis-full flex-wrap items-center justify-end gap-2 sm:w-auto sm:max-w-none sm:basis-auto">
      <Button variant="secondary" size="sm" onClick={onClear} aria-label="Clear discovered jobs">
        <Trash2 className="h-4 w-4" />
        <span className="hidden sm:inline">Clear</span>
      </Button>
      <Button
        variant="secondary"
        size="sm"
        onClick={onRemoveVisited}
        aria-label="Remove jobs you have already opened"
      >
        <EyeOff className="h-4 w-4" />
        <span className="hidden sm:inline">Remove visited</span>
      </Button>
      {scanning && onStopScan && (
        <Button variant="danger" size="sm" onClick={onStopScan}>
          <Square className="h-3.5 w-3.5 fill-current" />
          Stop
        </Button>
      )}
      {onApplyAll && applying && onStopApply && (
        <Button variant="danger" size="sm" onClick={onStopApply}>
          <Square className="h-3.5 w-3.5 fill-current" />
          Stop
        </Button>
      )}
      {onApplyAll && (
        <Button
          variant="secondary"
          size="sm"
          onClick={onApplyAll}
          disabled={scanning || applying}
          aria-label={applying ? 'Applying' : 'Apply all'}
        >
          <Send className={clsx('h-4 w-4', applying && 'animate-pulse')} />
          <span className="hidden sm:inline">{applying ? 'Applying' : 'Apply all'}</span>
        </Button>
      )}
      <Button size="sm" onClick={onScan} disabled={scanning || applying}>
        <RefreshCw className={clsx('h-4 w-4', scanning && 'animate-spin')} />
        {scanning ? 'Scanning' : 'Scan'}
      </Button>
    </div>
  );
}
