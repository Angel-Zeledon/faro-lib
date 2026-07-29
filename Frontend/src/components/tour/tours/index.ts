import type { TourDefinition } from '../types'

import { analystTour } from './analyst'
import { dataTour } from './data'
import { configTour } from './config'
import { hoyTour } from './hoy'
import { inventoryTour } from './inventory'
import { pedidosTour } from './pedidos'
import { inventorySetupTour } from './inventorySetup'
import { quickStartTour } from './quickStart'
import { roiTour } from './roi'
import { scenariosTour } from './scenarios'
import { sessionsTour } from './sessions'
import { settingsTour } from './settings'
import { skusTour } from './skus'
import { suppliersTour } from './suppliers'
import { usersTour } from './users'

/**
 * Every tour in the app, one module each.
 *
 * Order does not matter: `TourContext` resolves the tour for a route by
 * longest matching prefix, so `/inventory/roi` can have its own without
 * `/inventory`'s shadowing it.
 *
 * To add one: create a sibling module exporting a `TourDefinition` (steps plus
 * its own `es`/`en` copy — see types.ts for why the copy lives with the tour),
 * add the `data-tour` anchors to the screen, and list it here.
 */
export const TOURS: TourDefinition[] = [
  quickStartTour,
  scenariosTour,
  hoyTour,
  inventoryTour,
  inventorySetupTour,
  suppliersTour,
  roiTour,
  pedidosTour,
  skusTour,
  dataTour,
  usersTour,
  configTour,
  settingsTour,
  analystTour,
  sessionsTour,
]

/** The tour whose route best matches `pathname`, or null. */
export function tourForRoute(pathname: string | null): TourDefinition | null {
  if (!pathname) return null
  const matches = TOURS.filter(
    t => pathname === t.route || pathname.startsWith(`${t.route}/`),
  ).sort((a, b) => b.route.length - a.route.length)
  return matches[0] ?? null
}
