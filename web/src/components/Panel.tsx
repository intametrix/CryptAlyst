import { type ReactNode } from 'react';
import { usePersistentSize, useSplit } from '../hooks/usePersistentSize';
import type { ResizeAxis } from '../hooks/usePersistentSize';

/* --------------------------------------------------------------------- Panel */

/** The dashboard's card shell. Kept for the un-resizable cases (and headers). */
export function Panel({
  className,
  children,
  ...rest
}: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLElement>) {
  return (
    <section className={`panel${className !== undefined ? ` ${className}` : ''}`} {...rest}>
      {children}
    </section>
  );
}

/** Panel header: title, optional meta text, and an actions slot. */
export function PanelHead({
  title,
  meta,
  actions,
  titleNode,
}: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  titleNode?: ReactNode;
}) {
  return (
    <div className="panel-head">
      {titleNode ?? <h2>{title}</h2>}
      {meta !== undefined && meta !== null ? <div className="panel-meta">{meta}</div> : null}
      {actions !== undefined && actions !== null ? <div className="actions">{actions}</div> : null}
    </div>
  );
}

/* ------------------------------------------------------------ ResizablePanel */

interface GripProps {
  axis: ResizeAxis;
  onDrag: (event: React.PointerEvent<HTMLElement>) => void;
  onNudge: (dx: number, dy: number) => void;
  label: string;
}

/** One focusable, keyboard-operable resize grip. */
function Grip({ axis, onDrag, onNudge, label }: GripProps) {
  const onKeyDown = (event: React.KeyboardEvent<HTMLElement>): void => {
    const step = event.shiftKey ? 40 : 8;
    const x = axis === 'y' ? 0 : event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
    const y = axis === 'x' ? 0 : event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
    if (x === 0 && y === 0) return;
    event.preventDefault();
    onNudge(x, y);
  };
  return (
    <span
      role="separator"
      tabIndex={0}
      aria-label={label}
      aria-orientation={axis === 'y' ? 'horizontal' : 'vertical'}
      className={`grip grip-${axis}`}
      onPointerDown={onDrag}
      onKeyDown={onKeyDown}
    >
      <span className="grip-dots" aria-hidden="true" />
    </span>
  );
}

export interface ResizablePanelProps {
  /** Stable id — the `localStorage` key for this box's size. */
  id: string;
  /** Which axes the user may drag. */
  axis?: ResizeAxis;
  minWidth?: number;
  maxWidth?: number;
  minHeight?: number;
  maxHeight?: number;
  /** Keep a resolved height even when only the width is draggable. */
  fillHeight?: boolean;
  /** Show the corner grip (defaults to true). */
  corner?: boolean;
  /**
   * Fill the parent box on the non-draggable axis instead of sizing to content.
   * On the draggable axis, a stored size wins once the user has dragged once.
   */
  fill?: boolean;
  /** Size to open at before the user has ever dragged this box. */
  defaultWidth?: number;
  defaultHeight?: number;
  /** Extra class for the sized wrapper. */
  className?: string;
  /** Size-driven render function; `null` means "CSS decides". */
  children: (size: { width: number | null; height: number | null }) => ReactNode;
}

/**
 * A card/box the user can drag to resize, with the size persisted by `id`.
 *
 * The wrapper owns the explicit pixel size and clips its child; the grips are
 * real focusable separators, so the box can also be resized with the arrow keys
 * (Shift for a bigger step). Dragging uses pointer capture and disables text
 * selection for the duration — see `usePersistentSize`.
 */
export function ResizablePanel({
  id,
  axis = 'both',
  minWidth = 240,
  maxWidth,
  minHeight = 140,
  maxHeight,
  fillHeight = false,
  corner = true,
  fill = false,
  defaultWidth,
  defaultHeight,
  className,
  children,
}: ResizablePanelProps) {
  const { ref, size, startDrag, startDragX, startDragY, nudge, dragging } = usePersistentSize({
    id,
    axis,
    minWidth,
    maxWidth,
    minHeight,
    maxHeight,
    fillHeight,
    defaultWidth,
    defaultHeight,
  });

  const wantsW = axis === 'both' || axis === 'x';
  const wantsH = fillHeight || axis === 'both' || axis === 'y';
  const stretchW = fill && !wantsW;
  const stretchH = fill && !wantsH;

  const style: React.CSSProperties = {
    // `100%` before the first drag (so the box fills the row), the stored pixel
    // width afterwards. `flex: 1 1 0` lets it share the row with a sibling.
    width: stretchW ? '100%' : wantsW && size.w > 0 ? `${size.w}px` : undefined,
    height: stretchH ? '100%' : wantsH && size.h > 0 ? `${size.h}px` : undefined,
    flex: stretchW ? '1 1 0' : undefined,
    alignSelf: fill ? 'stretch' : undefined,
    maxWidth: wantsW && maxWidth !== undefined ? `${maxWidth}px` : undefined,
    maxHeight: wantsH && maxHeight !== undefined ? `${maxHeight}px` : undefined,
  };

  return (
    <div
      ref={ref}
      className={`resizable${dragging ? ' dragging' : ''}${className !== undefined ? ` ${className}` : ''}`}
      style={style}
      data-resize-id={id}
    >
      {children({
        width: wantsW && size.w > 0 ? size.w : null,
        height: wantsH && size.h > 0 ? size.h : null,
      })}
      {corner && axis === 'both' && (
        <Grip axis="both" label="resize panel (arrow keys, shift for bigger steps)" onDrag={startDrag} onNudge={nudge} />
      )}
      {corner && axis === 'x' && (
        <Grip axis="x" label="resize panel width" onDrag={startDragX} onNudge={nudge} />
      )}
      {corner && axis === 'y' && (
        <Grip axis="y" label="resize panel height" onDrag={startDragY} onNudge={nudge} />
      )}
    </div>
  );
}

/* ----------------------------------------------------------------- SplitPane */

export interface SplitPaneProps {
  /** Stable id for the persisted divider position. */
  id: string;
  /** Stack the children instead of laying them out side by side. */
  vertical?: boolean;
  /** Which side keeps the fixed size; the other takes the remainder. */
  side?: 'first' | 'second';
  /**
   * Pixels of the fixed side. In a **vertical** split the first pane instead
   * starts here and then *grows* with the container unless the user has dragged
   * the divider — a pinned pixel height would starve the chart of any extra
   * vertical space, which is exactly the bug this avoids.
   */
  defaultSize?: number;
  min?: number;
  max?: number;
  /** Extra class on the container. */
  /** Set the split's own height (a vertical split in a definite-height box). */
  fillHeight?: boolean;
  /** Extra class on the container. */
  className?: string;
  /** Class applied to each of the two children. */
  firstClassName?: string;
  secondClassName?: string;
  first: ReactNode;
  second: ReactNode;
}

/**
 * Two children with a draggable divider between them.
 *
 * `vertical` stacks them (used for the chart's price pane versus its indicator
 * panes); the default splits left/right (used for alerts versus the flow
 * leaderboard). The fixed side's size is persisted, and the divider is a
 * focusable separator that responds to the arrow keys.
 */
export function SplitPane({
  id,
  vertical = false,
  side = 'first',
  defaultSize = 520,
  min = 260,
  max,
  className,
  firstClassName,
  secondClassName,
  fillHeight = false,
  first,
  second,
}: SplitPaneProps) {
  const { size, ref, dragging, stored: hasStoredSize, startDrag, nudge } = useSplit({
    id,
    vertical,
    side,
    min,
    max,
    defaultSize,
  });

  const sizeClass = side === 'first' ? firstClassName : secondClassName;
  const otherClass = side === 'first' ? secondClassName : firstClassName;
  const sizeNode = side === 'first' ? first : second;
  const otherNode = side === 'first' ? second : first;

  /**
   * A vertical split lets the first pane absorb extra height by default. A
   * stored size means the user dragged the divider, so their choice wins from
   * then on; until then the pane is `flex: 1 1 0` and the second pane is sized
   * to its own content (the indicator panes are inside the chart's panes, not
   * this split, so they do not compete for this space at all).
   */
  const verticalGrow = vertical && !hasStoredSize && side === 'first';
  const fixedStyle: React.CSSProperties = verticalGrow
    ? { flex: '1 1 0', minHeight: `${min}px` }
    : side === 'first'
      ? vertical
        ? { height: `${size}px`, flex: '0 0 auto' }
        : { width: `${size}px` }
      : vertical
        ? { height: `${size}px`, flex: '0 0 auto' }
        : { width: `${size}px` };

  return (
    <div
      ref={ref}
      className={`split${vertical ? ' split-vertical' : ''}${dragging ? ' dragging' : ''}${fillHeight ? ' split-fill-height' : ''}${className !== undefined ? ` ${className}` : ''}`}
      data-split-id={id}
    >
      <div className={`split-child split-fixed${sizeClass !== undefined ? ` ${sizeClass}` : ''}`} style={fixedStyle}>
        {sizeNode}
      </div>
      <span
        role="separator"
        tabIndex={0}
        aria-label={vertical ? 'resize chart panes' : 'resize columns'}
        aria-orientation={vertical ? 'horizontal' : 'vertical'}
        className="split-divider"
        onPointerDown={startDrag}
        onKeyDown={(event) => {
          const step = event.shiftKey ? 40 : 12;
          const delta =
            (vertical ? event.key === 'ArrowUp' : event.key === 'ArrowLeft')
              ? -step
              : (vertical ? event.key === 'ArrowDown' : event.key === 'ArrowRight')
                ? step
                : 0;
          if (delta === 0) return;
          event.preventDefault();
          nudge(delta);
        }}
      >
        <span className="split-divider-grip" aria-hidden="true" />
      </span>
      <div className={`split-child split-fill${otherClass !== undefined ? ` ${otherClass}` : ''}`}>{otherNode}</div>
    </div>
  );
}
