import type { TourDefinition } from '../types'

export const analystTour: TourDefinition = {
  id: 'analyst-v1',
  route: '/analyst',
  name: 'name',
  steps: [
    { title: 'intro_title', body: 'intro_body' },
    { anchor: 'an.new', title: 'new_title', body: 'new_body' },
    { anchor: 'an.chats', title: 'chats_title', body: 'chats_body' },
    { anchor: 'an.start', title: 'start_title', body: 'start_body' },
    { title: 'limits_title', body: 'limits_body' },
  ],
  copy: {
    es: {
      name: 'Cómo usar el analista',
      intro_title: 'Preguntar por tus números en palabras',
      intro_body: 'El analista responde sobre lo que ya está dentro de Faro: los resultados de una actualización, qué tan bien le atinó cada modelo a cada producto, tu inventario, la calidad de los datos que subiste.\n\nNo es un buscador de internet. Si algo nunca lo subiste, aquí no hay respuesta — y te lo va a decir en vez de inventarla.',
      new_title: 'Una conversación por tema',
      new_body: 'Cada conversación arrastra sus mensajes anteriores como contexto. Eso es lo que te deja repreguntar («¿y ese mismo producto en diciembre?») sin volver a explicarlo todo.\n\nY es justo lo que estorba cuando cambias de tema: el hilo sigue cargando lo de antes y la respuesta sale contaminada. Abre una nueva cuando la pregunta no tenga nada que ver con la anterior.',
      chats_title: 'Lo que preguntaste no se pierde',
      chats_body: 'Cada conversación queda guardada con su última respuesta a la vista, y el buscador de arriba filtra por título.\n\nLa estrella sube a favoritos las que repites cada mes. El basurero borra: el hilo desaparece de la lista al instante y tienes unos segundos para deshacer, pero pasado ese momento ya no vuelve.',
      start_title: 'General o sobre tus datos',
      start_body: 'Una conversación puede correr en dos modos, y la diferencia es grande.\n\nSin sesión responde con conocimiento general: qué significa un término, cómo funciona la plataforma, cómo se lee una métrica. No mira tus datos.\n\nLigada a una actualización completada sí los mira: contesta con tus productos, tus cifras y tus riesgos de quiebre. Eso se elige en el selector «Sesión» que aparece arriba en cuanto abres una conversación, y se puede cambiar a media charla.',
      limits_title: 'Para qué sirve y para qué no',
      limits_body: 'Sirve para entender en lenguaje normal los números detrás de una recomendación: por qué este producto salió urgente, qué tan confiable fue el pronóstico en él, qué tan sucios venían sus datos.\n\nNo reemplaza al semáforo, que ya te da la cobertura y la cantidad exactas. Y no compra nada: la decisión sigue siendo tuya.',
    },
    en: {
      name: 'How to use the analyst',
      intro_title: 'Ask about your numbers in plain words',
      intro_body: 'The analyst answers about what is already inside Faro: the results of an update, how well each model did on each product, your inventory, the quality of the data you uploaded.\n\nIt is not an internet search. If you never uploaded something, there is no answer here — and it will tell you so rather than make one up.',
      new_title: 'One conversation per topic',
      new_body: 'Every conversation carries its earlier messages along as context. That is what lets you follow up ("and that same product in December?") without explaining it all again.\n\nAnd it is exactly what gets in the way when you switch topics: the thread keeps dragging the old subject along and the answer comes back contaminated. Start a new one when the question has nothing to do with the last.',
      chats_title: 'What you asked is not lost',
      chats_body: 'Every conversation is kept with its latest reply in view, and the search box above filters by title.\n\nThe star pins the ones you repeat every month to favourites. The bin deletes: the thread leaves the list immediately and you have a few seconds to undo, but after that moment it does not come back.',
      start_title: 'General, or about your data',
      start_body: 'A conversation can run in two modes, and the difference is a big one.\n\nWith no session it answers from general knowledge: what a term means, how the platform works, how to read a metric. It does not look at your data.\n\nTied to a completed update it does look: it answers with your products, your figures and your stockout risks. You choose that in the "Session" picker that appears at the top as soon as you open a conversation, and you can change it mid-chat.',
      limits_title: 'What it is for, and what it is not',
      limits_body: 'It is for understanding, in plain language, the numbers behind a recommendation: why this product came out urgent, how reliable the forecast was on it, how dirty its data was.\n\nIt does not replace the stock signal, which already gives you exact coverage and quantity. And it buys nothing: the decision stays yours.',
    },
  },
}
