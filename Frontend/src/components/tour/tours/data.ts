import type { TourDefinition } from '../types'

export const dataTour: TourDefinition = {
  id: 'data-v1',
  route: '/data',
  name: 'name',
  autoStart: true,
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'data.new', title: 'new_title', body: 'new_body' },
    { anchor: 'data.item', title: 'item_title', body: 'item_body' },
    { anchor: 'data.panel', title: 'panel_title', body: 'panel_body' },
    { title: 'ready_title', body: 'ready_body' },
  ],
  copy: {
    es: {
      name: 'Cómo preparar los datos de los que todo depende',
      intro_title: 'De aquí sale todo lo demás',
      intro_body: 'Esta pantalla es tu bodega de datos: los archivos y conexiones con los que se entrena todo lo que después lees como recomendación de compra.\n\nUn archivo mal armado casi nunca da un error. Da un pronóstico creíble y equivocado, que es mucho peor. Por eso el rato que pasas aquí rinde más que el que pasas ajustando modelos: es el único punto del flujo donde arreglas la causa en vez del síntoma.',
      new_title: 'Archivo o conexión: no da lo mismo',
      new_body: 'Un archivo es una foto del día que lo subiste; para que se actualice, alguien tiene que volver a subirlo. Una conexión a tu base se relee cada vez que entrenas.\n\nSi eliges archivo, la pregunta que lo decide todo es quién lo va a reemplazar el mes que viene y cuándo. Entrenar sobre datos de hace tres meses no falla ni avisa: te contesta con la misma seguridad sobre un mundo que ya cambió.',
      item_title: 'Revisa la ficha antes de fiarte del archivo',
      item_body: 'Filas, tamaño y estado. Suena aburrido y es el filtro más barato que tienes: una exportación cortada a la mitad, un archivo al que le faltan meses o una conexión que dejó de responder se ven aquí en dos segundos.\n\nDescubrirlo ahora te cuesta un minuto. Descubrirlo después de entrenar te cuesta un ciclo de compras completo, porque el pronóstico va a salir igual y va a parecer razonable.',
      panel_title: 'Qué puedes hacerle a una fuente',
      panel_body: 'Vista previa: mira las primeras filas con ojo desconfiado — fechas que quedaron como texto, una fila de totales colada al final, el mismo producto escrito de dos maneras (cada variante se vuelve un producto distinto y ninguna junta historia suficiente).\n\nAnálisis: cuánta historia tiene cada SKU antes de entrenar con él.\n\nEditar: corriges y se guarda como fuente nueva; el original no se toca nunca.\n\nReemplazar archivo: mismo registro, datos frescos.',
      ready_title: 'Cuándo están listos para entrenar',
      ready_body: 'No necesitas datos perfectos. Necesitas cuatro cosas: una fecha legible, la cantidad vendida, un identificador de producto que no cambie de un mes a otro, y suficientes meses seguidos para que se note tu temporada.\n\nCon eso ya puedes pasar a entrenar — es la otra pestaña de arriba, y va a leer estas mismas fuentes sin que tengas que volver a subir nada.',
    },
    en: {
      name: 'How to prepare the data everything depends on',
      intro_title: 'Everything else comes from here',
      intro_body: 'This screen is your data warehouse: the files and connections everything you later read as a purchase recommendation is trained on.\n\nA badly built file almost never throws an error. It gives a credible, wrong forecast, which is far worse. That is why the time you spend here pays better than the time you spend tuning models: it is the one point in the flow where you fix the cause instead of the symptom.',
      new_title: 'File or connection: not the same choice',
      new_body: 'A file is a snapshot of the day you uploaded it; for it to update, somebody has to upload it again. A connection to your database is re-read every time you train.\n\nIf you pick a file, the question that decides everything is who replaces it next month, and when. Training on three-month-old data does not fail and does not warn you: it answers with the same confidence about a world that has already moved on.',
      item_title: 'Check the card before you trust the file',
      item_body: 'Rows, size and status. It sounds dull and it is the cheapest filter you have: an export cut in half, a file missing months, or a connection that stopped answering all show up here in two seconds.\n\nCatching it now costs you a minute. Catching it after training costs you a whole purchasing cycle, because the forecast will come out anyway and it will look reasonable.',
      panel_title: 'What you can do to a source',
      panel_body: 'Preview: read the first rows with a suspicious eye — dates that ended up as text, a totals row slipped in at the bottom, the same product spelled two ways (each variant becomes a separate product and none gathers enough history).\n\nAnalysis: how much history each SKU has before you train on it.\n\nEdit: you fix it and it is saved as a new source; the original is never touched.\n\nReplace file: same record, fresh data.',
      ready_title: 'When the data is ready to train',
      ready_body: 'You do not need perfect data. You need four things: a readable date, the quantity sold, a product identifier that does not change from one month to the next, and enough consecutive months for your season to show.\n\nWith that you can move on to training — it is the other tab up top, and it will read these same sources without you uploading anything again.',
    },
  },
}
