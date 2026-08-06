"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { getEntitlements, type Entitlements } from "./api";

type Ctx = {
  ent: Entitlements | null;
  has: (f: string) => boolean;
  readOnly: boolean;
  loading: boolean;
};

// Fail CLOSED. `has` used to answer `true` whenever entitlements were absent —
// both before the request finished and after it failed — so a dropped
// /entitlements call showed a Starter tenant the whole Professional navigation
// (Mensajes, Asistente IA, Escenarios, Automatización). A permission check that
// opens up when it cannot verify is not a check; the backend then refused the
// calls and the user met a wall behind a link the app had just offered them.
// Callers that would rather wait than show a padlock read `loading`.
const EntitlementsContext = createContext<Ctx>({
  ent: null, has: () => false, readOnly: false, loading: true,
});

export function EntitlementsProvider({ children }: { children: React.ReactNode }) {
  const [ent, setEnt] = useState<Entitlements | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    getEntitlements().then(setEnt).catch(() => setEnt(null)).finally(() => setLoading(false));
  }, []);
  const has = (f: string) => (ent ? !!ent.features[f] : false);
  return (
    <EntitlementsContext.Provider
      value={{ ent, has, readOnly: ent?.read_only ?? false, loading }}
    >
      {children}
    </EntitlementsContext.Provider>
  );
}

export const useEntitlements = () => useContext(EntitlementsContext);
