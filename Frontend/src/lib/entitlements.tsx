"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { getEntitlements, type Entitlements } from "./api";

type Ctx = {
  ent: Entitlements | null;
  readOnly: boolean;
  loading: boolean;
  /** Re-read the ceilings. Call it after anything that consumes one, so a
   *  usage bar is never stale by a whole page load. */
  refresh: () => Promise<void>;
};

// This used to carry `has(feature)`, and it failed CLOSED on purpose: a dropped
// /entitlements call must not hand a tenant a navigation full of things the
// backend would then refuse. Both tiers include every feature now, so there is
// nothing left to hide — what this carries is *how much*: the tier, the
// ceilings, the usage against them, and the channels to ask for more.
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

/** The limits a user can see themselves approaching, in the order they hit them. */
export const LIMIT_KEYS = [
  "max_skus", "max_users", "max_locations", "max_sessions", "max_api_keys",
] as const;
