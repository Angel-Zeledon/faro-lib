import type { TourDefinition } from '../types'

export const inventorySetupTour: TourDefinition = {
  id: 'inventory-setup-v1',
  route: '/inventory-setup',
  name: 'name',
  // The one screen someone lands on with nothing to do yet: two empty panels
  // and no idea that they are allowed to stop early. That is what the tour is
  // for, so it offers itself.
  autoStart: true,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'setup.import', title: 'import_title', body: 'import_body' },
    { anchor: 'setup.gaps', title: 'gaps_title', body: 'gaps_body' },
    { title: 'assumptions_title', body: 'assumptions_body' },
  ],
  copy: {
    es: {
      name: 'Configurar sin configurarlo todo',
      intro_title: 'No tienes que llenar tus dos mil productos',
      intro_body: 'Para decirte cuánto pedir necesitamos saber, por producto, cuánto tienes hoy, cuánto te cuesta y cuántos días tarda tu proveedor. Pedirte eso para el catálogo entero es exactamente lo que hace que nadie termine.\n\nAsí que aquí no se ordena por nombre ni por código: se ordena por plata. Unas decenas de productos suelen llevarse la mayor parte de tu compra del mes, y con esos configurados ya compras bien.',
      import_title: 'Primero, el archivo que tu sistema ya exporta',
      import_body: 'Nadie va a renombrar encabezados para complacernos, así que aceptamos el archivo tal como sale de tu sistema: entendemos nombres de columna en español y números con coma.\n\nAntes de escribir nada te mostramos cómo leímos cada columna y cuántas filas quedan fuera y por qué. Si nos equivocamos, lo corriges ahí mismo.\n\nVale la pena mirarlo: un archivo mal leído no falla, mete stock falso — y con stock falso el semáforo te miente con toda confianza.',
      gaps_title: 'La lista está ordenada por plata',
      gaps_body: 'Arriba están los productos que se llevan la mayor parte de tu compra del mes, y la columna acumulada te dice dónde puedes parar: cuando llegues al 80%, ya tienes casi todo el beneficio.\n\nMira la barra con cuidado — mide plata, no filas. Puede ir en 82% con la mayoría de los renglones todavía vacíos, y eso está bien.\n\nCada fila se llena y se guarda aquí mismo: stock, costo y días de entrega, sin salir de la pantalla ni abrir otro producto.',
      assumptions_title: 'Lo que no nos des, lo suponemos',
      assumptions_body: 'Sin costo seguimos ordenando por volumen en vez de por plata, y no podemos decirte cuánto vale la compra. Sin días de entrega usamos un valor por defecto que nadie eligió, y el semáforo se corre. Sin stock no hay cobertura que calcular.\n\nNunca lo escondemos: cada supuesto queda marcado como supuesto en el panel de compras. Pero un dato tuyo vale más que una suposición nuestra.\n\nPuedes irte a la mitad y volver: la barra te espera donde la dejaste.',
    },
    en: {
      name: 'Setting up without setting up everything',
      intro_title: 'You do not have to fill in two thousand products',
      intro_body: 'To tell you how much to order we need to know, per product, how much you have today, what it costs you and how many days your supplier takes. Asking you for that across the whole catalogue is exactly what makes people give up.\n\nSo nothing here is sorted by name or code: it is sorted by money. A few dozen products usually carry most of your monthly purchase, and with those configured you already buy well.',
      import_title: 'First, the file your system already exports',
      import_body: 'Nobody is going to rename headers to please us, so we take the file exactly as your system produces it: we read Spanish column names and comma decimals.\n\nBefore anything is written we show you how we read each column, and how many rows are left out and why. If we got it wrong, you fix it right there.\n\nIt is worth looking at: a misread file does not fail, it writes wrong stock — and with wrong stock the signal lies to you with complete confidence.',
      gaps_title: 'The list is ranked by money',
      gaps_body: 'At the top are the products that carry most of this month\'s purchase, and the cumulative column tells you where you may stop: once you reach 80%, you already have almost all the benefit.\n\nRead the bar carefully — it measures money, not rows. It can sit at 82% with most lines still empty, and that is fine.\n\nEvery row is filled in and saved right here: stock, cost and lead time, without leaving the screen or opening another product.',
      assumptions_title: 'Whatever you skip, we assume',
      assumptions_body: 'With no cost we keep ranking by volume instead of money, and cannot tell you what the purchase is worth. With no lead time we use a default nobody chose, and the signal shifts. With no stock there is no coverage to calculate.\n\nWe never hide it: every assumption is flagged as an assumption on the purchasing panel. But a figure from you beats a guess from us.\n\nYou can leave halfway and come back: the bar waits where you left it.',
    },
  },
}
