"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { getEntitlements, type Entitlements } from "./api";

type Ctx = {
  ent: Entitlements | null;
  has: (f: string) => boolean;
  readOnly: boolean;
  loading: boolean;
};

const EntitlementsContext = createContext<Ctx>({
  ent: null, has: () => true, readOnly: false, loading: true,
});

export function EntitlementsProvider({ children }: { children: React.ReactNode }) {
  const [ent, setEnt] = useState<Entitlements | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    getEntitlements().then(setEnt).catch(() => setEnt(null)).finally(() => setLoading(false));
  }, []);
  const has = (f: string) => (ent ? !!ent.features[f] : true);
  return (
    <EntitlementsContext.Provider
      value={{ ent, has, readOnly: ent?.read_only ?? false, loading }}
    >
      {children}
    </EntitlementsContext.Provider>
  );
}

export const useEntitlements = () => useContext(EntitlementsContext);
