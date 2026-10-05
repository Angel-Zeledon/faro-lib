"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { getEntitlements, type Entitlements, type PlanFeature } from "./api";

type Ctx = {
  ent: Entitlements | null;
  readOnly: boolean;
  loading: boolean;
  /** Re-read the ceilings. Call it after anything that consumes one, so a
   *  usage bar is never stale by a whole page load. */
  refresh: () => Promise<void>;
};

// Navigation is never hidden by plan. What this carries is *how much* (the
// tier, the ceilings, the usage) and, since 2026-10-05, which of the three
// machine-facing channels the plan includes (`useFeature`), so a screen can
// show them locked with a way to ask, instead of letting a call fail.
//
// It still fails closed in the way that matters. With no answer, `ent` is null
// and the UI shows no limits at all rather than inventing generous ones.
const EntitlementsContext = createContext<Ctx>({
  ent: null, readOnly: false, loading: true, refresh: async () => {},
});

export function EntitlementsProvider({ children }: { children: React.ReactNode }) {
  const [ent, setEnt] = useState<Entitlements | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setEnt(await getEntitlements());
    } catch {
      setEnt(null);
    }
  }, []);

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  return (
    <EntitlementsContext.Provider
      value={{ ent, readOnly: ent?.read_only ?? false, loading, refresh }}
    >
      {children}
    </EntitlementsContext.Provider>
  );
}

export const useEntitlements = () => useContext(EntitlementsContext);

/**
 * Whether the plan includes a machine-facing channel (API, MCP, WhatsApp bot).
 * `locked` is true ONLY when the backend said so explicitly: with no answer yet,
 * a dropped call or an older backend, nothing is drawn locked — the backend
 * still refuses the call (`plan_feature_locked`) and the error bridge handles
 * that. A paid or corporate tenant therefore never sees a locked state flash.
 */
export function useFeature(feature: PlanFeature): { locked: boolean } {
  const { ent } = useEntitlements();
  return { locked: ent?.features?.[feature] === false };
}

/** The limits a user can see themselves approaching, in the order they hit them. */
export const LIMIT_KEYS = [
  "max_skus", "max_users", "max_locations", "max_sessions", "max_api_keys",
] as const;
