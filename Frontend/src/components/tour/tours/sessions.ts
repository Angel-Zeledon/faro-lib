import type { TourDefinition } from '../types'

export const sessionsTour: TourDefinition = {
  id: 'sessions-v1',
  route: '/sessions',
  name: 'name',
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'ses.row', title: 'row_title', body: 'row_body' },
    { anchor: 'ses.status', title: 'status_title', body: 'status_body' },
    { anchor: 'ses.dataset', title: 'dataset_title', body: 'dataset_body' },
    { anchor: 'ses.actions', title: 'actions_title', body: 'actions_body' },
  ],
  copy: {
    es: {
      name: 'Cómo funciona el historial',
      intro_title: 'Cada vez que actualizaste, aquí queda',
      intro_body: 'Esta lista no es un registro decorativo. Cada fila es el cálculo completo que Faro hizo con tus ventas, y es la pieza a la que apunta todo lo demás.\n\nEl semáforo de stock, las proyecciones por producto y los escenarios se calculan siempre a partir de una de estas filas. Cuando dos pantallas te muestran números distintos, casi siempre están leyendo actualizaciones distintas.',
      row_title: 'Una fila es un cálculo entero',
      row_body: 'Dentro de cada una va todo junto: el archivo que subiste, los ajustes con que se calculó, los modelos que ganaron y la proyección de cada producto.\n\nHaz clic en una fila completada y se abren sus resultados producto por producto. Las que no terminaron no abren nada, porque no hay resultados que ver.',
      status_title: 'Qué te dice el estado',
      status_body: 'Completada es la única que sirve para trabajar: es la que puedes elegir en el semáforo, en los escenarios y en el analista.\n\nEn cola y Calculando van en camino — el cálculo corre por detrás y puedes irte de esta pantalla.\n\nFallida no se borra sola a propósito: la fila se queda para que veas cuál archivo y cuáles ajustes se rompieron. Y una que está calculando no se deja eliminar, porque el trabajo sigue corriendo.',
      dataset_title: 'De qué archivo salió cada número',
      dataset_body: 'Esta columna es la trazabilidad: cuando una proyección se ve rara, aquí ves con qué datos se hizo.\n\nTener varias filas del mismo archivo es normal y sano — una diaria, otra semanal, otra con más horizonte — porque cambiar esos ajustes cambia los resultados. Por eso conviene renombrarlas con algo que signifique algo: «demo1» dentro de tres meses no te dice nada.',
      actions_title: 'Renombrar es barato, eliminar no',
      actions_body: 'El lápiz sólo cambia el nombre. Nada se recalcula y ningún número se mueve.\n\nEl basurero se lleva la fila entera: sus proyecciones, sus métricas de precisión y los archivos que generó. Lo que apuntaba a ella —el semáforo, un escenario, una conversación del analista— se queda sin base y hay que volver a calcular. No hay deshacer.',
    },
    en: {
      name: 'How the history works',
      intro_title: 'Every update you ran stays here',
      intro_body: 'This list is not a decorative log. Each row is the complete calculation Faro ran on your sales, and it is the piece everything else points at.\n\nThe stock signal, the per-product projections and the scenarios are always computed from one of these rows. When two screens show you different numbers, they are almost always reading different updates.',
      row_title: 'A row is one whole calculation',
      row_body: 'Each one bundles it all together: the file you uploaded, the settings it was computed with, the models that won, and the projection for every product.\n\nClick a completed row and its product-by-product results open. The ones that did not finish open nothing, because there are no results to see.',
      status_title: 'What the status tells you',
      status_body: 'Completed is the only one you can work from: it is the one you can pick in the stock signal, in scenarios and in the analyst.\n\nQueued and Calculating are on their way — the work runs in the background and you can leave this screen.\n\nFailed is deliberately not cleaned up: the row stays so you can see which file and which settings broke. And one that is still calculating cannot be deleted, because the job is still running.',
      dataset_title: 'Which file each number came from',
      dataset_body: 'This column is the traceability: when a projection looks odd, this is where you see what data produced it.\n\nHaving several rows from the same file is normal and healthy — one daily, one weekly, one with a longer horizon — because changing those settings changes the results. Which is why it pays to rename them into something meaningful: "demo1" will tell you nothing three months from now.',
      actions_title: 'Renaming is cheap, deleting is not',
      actions_body: 'The pencil only changes the name. Nothing is recomputed and no number moves.\n\nThe bin takes the whole row: its projections, its accuracy metrics and the files it generated. Whatever pointed at it — the stock signal, a scenario, an analyst conversation — is left with no base, and you have to run the calculation again. There is no undo.',
    },
  },
}
