import { createContext, useContext, type ReactNode } from 'react';
import type { ToolsMsg } from './types';

/**
 * The glossary, available where it is used instead of passed where it is not.
 *
 * `tools` was threaded through fourteen components purely so a leaf could render a
 * tooltip. Every one of those components had to declare the prop, forward it, and be
 * re-rendered when it changed — none of them used it. A context is the honest shape
 * for "whole-app constant that leaves read".
 */
const ToolsContext = createContext<ToolsMsg | null>(null);

export function ToolsProvider({ tools, children }: { tools: ToolsMsg | null; children: ReactNode }) {
  return <ToolsContext.Provider value={tools}>{children}</ToolsContext.Provider>;
}

/** The glossary the app loaded, or null before it arrives. */
export function useTools(): ToolsMsg | null {
  return useContext(ToolsContext);
}
