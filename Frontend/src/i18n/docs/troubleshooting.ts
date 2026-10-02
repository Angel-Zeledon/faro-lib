// Help center — Solución de problemas. Each problem is quoted in the words the
// app actually shows (Frontend/src/i18n/translations.ts `errors.*`,
// stockSetup.ts, serviceConfig.ts), then why it happens and what to do.
// Facts read from the code on 2026-10-02:
//   backend/dataframes/io.py           NUL-byte refusal (English-only message)
//   backend/api/v1/inventory.py        rows with a NUL skipped; ambiguous decimal → 422
//   ForecastingCore data/quality.py    products under 20 periods dropped
//   backend/inventory/service.py       SIN_DATOS = no forecast or no stock row
//   backend/api/v1/auth.py             verification link 24 h; reset code 15 min
//   backend/trial/service.py           trial 24 h, erased after expiry
//   backend/entitlements/plans.py      the free plan's ceilings
import type { DocSectionContent } from '@/i18n/docs/types'
import type { DocPageIdOf } from '@/i18n/docs/tree'

export const TROUBLESHOOTING: DocSectionContent<DocPageIdOf<'solucion-de-problemas'>> = {
  es: {
    'solucion-de-problemas/archivo-rechazado': {
      title: 'StockAI no acepta mi archivo',
      nav: 'Archivo rechazado',
      description:
        'Qué significa cada aviso al subir ventas o inventario, por qué aparece y qué cambiar en el archivo para que entre.',
      blocks: [
        { t: 'p', text: 'Casi todos los rechazos tienen arreglo en el propio archivo. Busca abajo el mensaje que ves; cada uno trae qué significa y qué hacer.' },

        { t: 'h2', id: 'unsupported-format', text: 'Formato no soportado' },
        { t: 'p', text: '**Lo que ves:** «No podemos leer archivos .pdf. Sube uno de estos formatos: …» (con la extensión de tu archivo).' },
        { t: 'p', text: '**Por qué:** las ventas se aceptan en CSV, Excel (`.xlsx`, `.xls`), Parquet y JSON. Un PDF, una imagen o un archivo comprimido no se pueden leer como tabla.' },
        { t: 'p', text: '**Qué hacer:** exporta el reporte desde tu sistema como CSV o Excel. Si no sabes por dónde empezar, descarga la [plantilla](/plantilla_stockai.csv): sus columnas son `sku`, `fecha`, `demanda`, `tienda`, `inventario`, `costo` y `precio`, y solo las tres primeras son obligatorias.' },

        { t: 'h2', id: 'too-large', text: 'El archivo pesa demasiado' },
        { t: 'p', text: '**Lo que ves:** «El archivo pesa … MB y el máximo es … MB. Sube uno más liviano o divídelo en varios.»' },
        { t: 'p', text: '**Por qué:** en el plan gratis cada archivo puede pesar hasta 25 MB; en el plan completo, hasta 2000 MB. Ver [Límites del plan gratis](/docs/administracion/limites-del-plan).' },
        { t: 'p', text: '**Qué hacer:** quita columnas que StockAI no usa (descripciones largas, direcciones, notas), guárdalo como CSV en vez de Excel, que pesa menos, o súbelo en partes. Si tu historial de verdad no cabe, escríbenos desde el aviso.' },

        { t: 'h2', id: 'empty-file', text: 'El archivo está vacío' },
        { t: 'p', text: '**Lo que ves:** «El archivo de «…» está vacío o no tiene columnas. Sube un CSV con encabezado y datos.» En el importador de inventario: «No pudimos usar ninguna fila del archivo. Revisa que tenga una columna con el código del producto.»' },
        { t: 'p', text: '**Por qué:** el archivo no tiene fila de encabezado, no tiene filas de datos, o ninguna fila trae el código del producto.' },
        { t: 'p', text: '**Qué hacer:** ábrelo y comprueba que la primera fila sean los nombres de las columnas y que debajo haya datos. Si tu sistema exporta un título o un logo encima de la tabla, bórralo.' },

        { t: 'h2', id: 'unreadable', text: 'No se puede leer' },
        { t: 'p', text: '**Lo que ves:** «No pudimos leer el archivo que subiste. Revisa que sea un CSV o Excel válido y vuelve a intentarlo.» En inventario: «No pudimos abrir el archivo como hoja de cálculo. Vuelve a exportarlo en .xlsx o .csv.»' },
        { t: 'p', text: '**Por qué:** el archivo está dañado, se cortó al descargarlo, o tiene una extensión que no corresponde a su contenido (por ejemplo, un HTML renombrado a `.xls`, que es como algunos sistemas «exportan a Excel»).' },
        { t: 'p', text: '**Qué hacer:** vuelve a exportarlo desde tu sistema, ábrelo en tu hoja de cálculo para confirmar que se ve bien, y guárdalo de nuevo como `.xlsx` o `.csv`.' },

        { t: 'h2', id: 'nul-byte', text: 'El archivo contiene un byte NUL' },
        { t: 'p', text: '**Lo que ves:** un aviso de que el archivo no se pudo leer, con el detalle técnico en inglés: «The file contains a NUL byte (first seen on line N). Rows containing one cannot be imported, because the value would be silently truncated. Re-export the file from your system.»' },
        { t: 'p', text: '**Por qué:** un byte NUL es un carácter invisible (código cero) que no debería estar en un archivo de texto. Suele aparecer cuando un sistema exporta en una codificación rara, cuando el archivo quedó a medio escribir, o cuando se copió desde una base de datos que guarda campos de largo fijo. Si StockAI lo dejara pasar, el valor de esa celda se cortaría sin avisar, así que prefiere rechazarlo. El número de línea te dice dónde aparece el primero.' },
        { t: 'p', text: '**Qué hacer:** vuelve a exportar el archivo desde tu sistema, de preferencia como CSV en UTF-8. Si el problema sigue, ábrelo en tu hoja de cálculo y guárdalo con «Guardar como» CSV: eso descarta los caracteres invisibles. En el importador de inventario no se rechaza el archivo entero: las filas con un byte NUL se saltan y se cuentan entre las que quedan fuera.' },

        { t: 'h2', id: 'ambiguous-number', text: '«1.250»: ¿mil doscientos cincuenta o uno coma veinticinco?' },
        { t: 'p', text: '**Lo que ves:** en el importador de inventario, la pregunta «Tu archivo escribe números como 1.250. ¿Cuánto es?»; al subir ventas, el hallazgo «No sabemos si la coma separa miles o decimales».' },
        { t: 'p', text: '**Por qué:** en América Latina «1.250» suele ser mil doscientos cincuenta; en un sistema configurado en inglés, uno coma veinticinco. Si nada en el archivo lo aclara, StockAI no adivina: un stock de 1250 leído como 1,25 pone en rojo una bodega llena.' },
        { t: 'p', text: '**Qué hacer:** elige la opción que corresponde a tu sistema. Si no estás seguro, busca en el archivo un número que conozcas, como el stock de un producto que contaste ayer. Si tu integración sube el inventario por la API, la respuesta es un `422` con el código `inventory_import_number_format_unclear`: «Tu archivo escribe números como …, y nada en él dice si eso es … o …. Súbelo desde Configurar inventario para elegir, o arréglalo en tu sistema.» La integración puede responder la pregunta indicando el formato en la llamada (ver [la referencia](/desarrolladores)).' },

        { t: 'h2', id: 'rows-left-out', text: 'Filas que quedan fuera del inventario' },
        { t: 'p', text: '**Lo que ves:** en el resumen del importador, «Filas con problemas que quedan fuera: …» y «Filas sin código de producto que se saltan: …», con ejemplos del tipo «Fila 214 (SKU-001): "N/D"».' },
        { t: 'p', text: '**Por qué:** una fila sin código de producto no se puede asignar a nada. Una fila con texto donde va un número («N/D», «agotado»), con stock o costo negativos, o con un byte NUL, se deja fuera en vez de inventar un valor. Si un producto aparece varias veces, se queda el valor de la última fila.' },
        { t: 'p', text: '**Qué hacer:** corrige las filas que te nombra el resumen y vuelve a importar. Si ya corregiste algunos productos a mano en StockAI, marca «No sobrescribir lo que corregí a mano» para que el archivo solo llene lo que falta.' },

        { t: 'h2', id: 'decide-first', text: 'Tienes que decidir algo antes de seguir' },
        { t: 'p', text: '**Lo que ves:** al confirmar las columnas de tus ventas, el bloque «Tienes que decidir algo antes de seguir», con el botón de continuar apagado. Si intentas entrenar igual: «Antes de entrenar tienes que decidir qué hacer con los problemas que encontramos en el archivo…».' },
        { t: 'p', text: '**Por qué:** el control de datos encontró algo que cambiaría el pronóstico sin avisarte, como fechas que pueden ser día/mes o mes/día, o una coma que puede ser de miles o de decimales.' },
        { t: 'p', text: '**Qué hacer:** responde cada pregunta. Cada opción dice qué hace y qué te cuesta si te equivocas; la marcada como recomendada es solo una sugerencia. Cuando no quede ninguna sin responder, el botón se enciende solo.' },
        { t: 'p', text: 'Si el mensaje es «Este archivo no puede generar un pronóstico, y no hay corrección que lo cambie. Revisa el archivo y vuelve a subirlo.», no hay pregunta que lo arregle: suele ser un mapeo de columnas equivocado (por ejemplo, la columna de cantidad apunta a un texto). Corrige el mapeo o sube otro archivo.' },

        { t: 'h2', id: 'short-history', text: 'Productos que no aparecen después de entrenar' },
        { t: 'p', text: '**Lo que ves:** en Inventario, el aviso de que algunos productos subidos no se incluyeron en el pronóstico, con el motivo «Solo … registros de historia (se necesitan al menos 20)».' },
        { t: 'p', text: '**Por qué:** un producto necesita al menos **20 períodos** de historia para entrar al pronóstico. Con menos no se puede medir si un modelo acierta, así que se deja fuera en lugar de darte un pronóstico sin respaldo.' },
        { t: 'p', text: '**Qué hacer:** si el producto es nuevo, espera a tener más historia. Si tienes historia más vieja en otro archivo, súbela junta. Si vendes poco cada día, prueba un detalle semanal o mensual: ver [Granularidad](/docs/conceptos/granularidad). Estos productos no afectan al resto de tus recomendaciones.' },
      ],
    },

    'solucion-de-problemas/sin-datos': {
      title: 'Un producto dice «Sin datos»',
      nav: 'Sin datos',
      description:
        '«Sin datos» no es un error ni un producto tranquilo: es un producto al que le falta algo para poder calcular su señal. Aquí está qué le falta y cómo dárselo.',
      blocks: [
        { t: 'p', text: 'El [semáforo](/docs/conceptos/semaforo) necesita dos cosas de cada producto: un **pronóstico** (cuánto vas a vender) y un **stock registrado** (cuánto tienes). Si falta cualquiera de las dos, el producto sale como «Sin datos»: no recibe señal, no tiene cobertura y no se le sugiere cantidad a pedir.' },
        { t: 'note', tone: 'warn', text: '«Sin datos» quiere decir «no sabemos», no «está bien». Un producto sin stock registrado podría estar agotado ahora mismo. Por eso StockAI no lo pinta de verde ni lo cuenta como riesgo cero.' },

        { t: 'h2', id: 'no-stock', text: 'Nunca registraste su stock' },
        { t: 'p', text: '**Lo que ves:** «Sin datos» en la señal y «Sin registro» en la columna de stock.' },
        { t: 'p', text: '**Por qué:** StockAI no inventa ceros. Un cero inventado es indistinguible de uno contado y pondría en rojo una bodega llena, así que mientras no le digas cuánto tienes, el producto queda sin señal.' },
        { t: 'p', text: '**Qué hacer:** carga el stock en [Configurar inventario](app:/configurar-inventario), subiendo el archivo de tu sistema o llenándolo a mano en «Empieza por estos». También puedes editarlo en la ficha del producto en Inventario. Ver [Configurar inventario](/docs/primeros-pasos/configurar-inventario).' },

        { t: 'h2', id: 'no-stock-warehouse', text: 'No tiene stock registrado en esa bodega' },
        { t: 'p', text: '**Lo que ves:** al mirar una bodega en particular, «Sin datos» con la nota «Nunca registraste stock de este producto en esta bodega. No decimos que haya cero: no lo sabemos.»' },
        { t: 'p', text: '**Por qué:** con varias bodegas, el stock se registra por bodega. Si tu archivo o tu sistema solo trae el stock de la bodega principal, las demás no tienen dato. Antes eso se leía como cero y StockAI sugería reponer cada sucursal mientras la mercadería estaba en la principal; ahora se muestra como lo que es: un dato que falta.' },
        { t: 'p', text: '**Qué hacer:** registra el stock de ese producto en esa bodega, aunque sea cero si de verdad está vacía. Ver [Bodegas y traslados](/docs/uso-diario/bodegas-y-traslados).' },

        { t: 'h2', id: 'no-forecast', text: 'No tiene pronóstico' },
        { t: 'p', text: '**Lo que ves:** «Sin datos» en un producto que sí tiene stock.' },
        { t: 'p', text: '**Por qué:** el producto no está en la actualización activa, por una de estas razones:' },
        { t: 'ul', items: [
          'Tiene menos de **20 períodos** de historia y se dejó fuera antes de entrenar. Inventario lo dice en el aviso de productos que no se incluyeron en el pronóstico, con el motivo.',
          'No venía en el archivo de ventas con el que se entrenó la actualización activa (por ejemplo, es un producto nuevo que agregaste después).',
          'No se pudo generar un pronóstico confiable para él.',
        ] },
        { t: 'p', text: '**Qué hacer:** si es un producto nuevo, inclúyelo en tu próxima carga de ventas. Si le falta historia, espera a tenerla o prueba un detalle semanal o mensual ([Granularidad](/docs/conceptos/granularidad)). Si crees que debería estar, revisa en [Historial](/docs/analisis/historial) qué archivo usó la actualización activa.' },

        { t: 'h2', id: 'estimated', text: 'La etiqueta «estimado»' },
        { t: 'p', text: 'No es lo mismo que «Sin datos». Un producto con señal puede tener valores marcados como **«estimado»**: son supuestos de StockAI porque nadie los configuró. Los más comunes:' },
        { t: 'dl', items: [
          ['Tiempo de entrega', '15 días, mientras no lo configures en el producto o en su proveedor, y hasta que StockAI lo aprenda de 3 recepciones. Ver [Tiempo de entrega aprendido](/docs/conceptos/tiempo-de-entrega-aprendido).'],
          ['Nivel de servicio', '95%, mientras no lo cambies. Ver [Stock de seguridad](/docs/conceptos/stock-de-seguridad).'],
          ['Compra mínima (MOQ)', '1 unidad.'],
        ] },
        { t: 'p', text: 'La señal se calcula igual, pero sobre supuestos. Un proveedor que en realidad tarda 45 días te va a avisar tarde si StockAI sigue suponiendo 15. En cuanto registras el dato real, la etiqueta desaparece.' },

        { t: 'h2', id: 'no-cost', text: 'El valor aparece como «—»' },
        { t: 'p', text: 'Cuando falta el **costo**, StockAI no puede valorar el stock ni la orden, y muestra «—» en lugar de cero. El producto sigue teniendo señal; lo que falta es su valor en dinero. Cárgale el costo en Configurar inventario y las cifras se completan.' },
      ],
    },

    'solucion-de-problemas/cuenta-y-acceso': {
      title: 'Problemas con la cuenta y el acceso',
      nav: 'Cuenta y acceso',
      description:
        'Correo sin verificar, contraseña olvidada, cuenta de prueba vencida, límite del plan, permisos y otros avisos que te impiden hacer algo.',
      blocks: [
        { t: 'h2', id: 'email-not-verified', text: 'Todavía no has verificado tu correo' },
        { t: 'p', text: '**Lo que ves:** una franja ámbar arriba de la app con «Reenviar correo», o el mensaje «Todavía no has verificado tu correo. Abre el enlace que te enviamos para activar tu cuenta.»' },
        { t: 'p', text: '**Por qué:** puedes entrar, subir archivos, entrenar y ver el semáforo sin verificar. Lo que queda bloqueado son las acciones que salen de tu empresa: **invitar personas** y **enviar órdenes a proveedores** por correo o WhatsApp. Una llave de API tampoco puede enviar nada fuera mientras ningún administrador tenga el correo verificado.' },
        { t: 'p', text: '**Qué hacer:** abre el enlace del correo que te mandamos al registrarte. Si no lo encuentras, revisa la carpeta de spam y luego pulsa «Reenviar correo».' },

        { t: 'h2', id: 'link-expired', text: 'El enlace de verificación venció' },
        { t: 'p', text: '**Lo que ves:** «El enlace de verificación no es válido o ya venció. Pide uno nuevo aquí abajo.»' },
        { t: 'p', text: '**Por qué:** el enlace dura 24 horas y sirve una sola vez.' },
        { t: 'p', text: '**Qué hacer:** en esa misma pantalla, «¿Se venció el enlace? Escribe tu correo y te mandamos uno nuevo.»' },

        { t: 'h2', id: 'forgot-password', text: 'Olvidé mi contraseña' },
        { t: 'steps', items: [
          'En la pantalla de inicio de sesión pulsa «¿Olvidaste tu contraseña?».',
          'Escribe tu correo. Si existe una cuenta, te llega un código de 6 dígitos; la pantalla no dice si el correo existe, a propósito.',
          'Escribe el código. Vence en 15 minutos y después de 5 intentos fallidos deja de servir: pide otro.',
          'Define tu nueva contraseña: al menos 8 caracteres, con al menos una letra y un número.',
        ] },

        { t: 'h2', id: 'trial-expired', text: 'La cuenta de prueba venció' },
        { t: 'p', text: '**Lo que ves:** al iniciar sesión, «Esta cuenta de prueba ya venció. Puedes crear otra desde la página principal.»' },
        { t: 'p', text: '**Por qué:** una cuenta de prueba dura 24 horas. Al vencer se borra, con todo lo que subiste.' },
        { t: 'p', text: '**Qué hacer:** crea otra desde la página principal, o [crea tu cuenta](/docs/primeros-pasos/crear-tu-cuenta) gratis para quedarte con tus datos. Si nos escribiste desde la cuenta de prueba y todavía no te respondimos, no la borramos hasta hacerlo.' },
        { t: 'p', text: 'Otros avisos al pedir una cuenta de prueba:' },
        { t: 'ul', items: [
          '«Ahora mismo no hay cuentas de prueba disponibles. Prueba de nuevo en un rato.» — hay demasiadas activas en este momento.',
          '«Ya creaste varias cuentas de prueba hoy desde esta conexión. Usa la que ya tienes o vuelve mañana.» — se permiten 3 por conexión al día.',
        ] },

        { t: 'h2', id: 'plan-limit', text: 'Llegaste al límite de tu plan gratis' },
        { t: 'p', text: '**Lo que ves:** el cuadro «Llegaste al límite de tu plan gratis», o el mensaje «Llegaste al límite de tu plan gratis: … de …. Escríbenos para ampliarlo.»' },
        { t: 'p', text: '**Por qué:** el plan gratis tiene todas las funciones, pero topes de cuánto cabe: 100 productos, 2 usuarios, 1 bodega, 3 pronósticos guardados, 1 llave de API y archivos de hasta 25 MB. Ver [Límites del plan gratis](/docs/administracion/limites-del-plan).' },
        { t: 'p', text: '**Qué hacer:** libera espacio (por ejemplo, borra un pronóstico guardado que ya no uses en [Historial](/docs/analisis/historial)) o escríbenos desde el mismo cuadro, por WhatsApp, correo o el formulario. No hay nada que comprar en la pantalla: ampliar el plan es una conversación.' },

        { t: 'h2', id: 'role', text: 'Tu rol no puede hacer esto' },
        { t: 'p', text: '**Lo que ves:** «Tu rol no puede hacer esto. Pídele a un administrador de tu empresa que lo haga, o que te cambie el rol.»' },
        { t: 'p', text: '**Por qué:** con rol de **solo lectura** ves todo pero no cambias nada: no subes archivos, no entrenas, no editas stock, no registras pedidos. Un **analista** hace todo eso, pero no administra usuarios. Ver [Usuarios y roles](/docs/administracion/usuarios-y-roles).' },
        { t: 'p', text: '**Qué hacer:** pídele a un administrador de tu empresa que haga la acción o que te cambie el rol en Usuarios.' },

        { t: 'h2', id: 'too-many-attempts', text: 'Demasiados intentos' },
        { t: 'p', text: '**Lo que ves:** «Demasiados intentos. Espera unos minutos y prueba de nuevo.» En el asistente: «Demasiadas solicitudes — espera un momento antes de enviar otro mensaje.»' },
        { t: 'p', text: '**Por qué:** StockAI limita los intentos seguidos de inicio de sesión y de códigos, para que nadie pueda adivinar una contraseña. El asistente admite 20 mensajes por minuto para toda la empresa.' },
        { t: 'p', text: '**Qué hacer:** espera unos minutos y vuelve a intentarlo.' },

        { t: 'h2', id: 'assistant-unavailable', text: 'El asistente no está disponible' },
        { t: 'p', text: '**Lo que ves:** en el asistente, «Esta instalación no tiene configurado un modelo de lenguaje, así que el asistente no puede responder…».' },
        { t: 'p', text: '**Por qué:** el asistente necesita una llave de DeepSeek en la instalación, y esta no la tiene. El resto de StockAI — pronósticos, semáforo y órdenes de compra — no depende de él y funciona igual.' },
        { t: 'p', text: '**Qué hacer:** quien administra el servidor la activa en [Instalación](/docs/administracion/instalacion), en la tarjeta del asistente.' },

        { t: 'h2', id: 'not-operator', text: 'Esta cuenta no opera la instalación' },
        { t: 'p', text: '**Lo que ves:** en Instalación, pestaña «Esta instalación», el aviso «Esta cuenta no opera la instalación».' },
        { t: 'p', text: '**Por qué:** la configuración de los servicios del servidor (correo, WhatsApp, asistente) pertenece a quien administra el despliegue, no al rol de administrador de una empresa. Esas personas se nombran con la variable `INSTANCE_ADMIN_EMAILS` del servidor.' },
        { t: 'p', text: '**Qué hacer:** si eres administrador de tu empresa, puedes configurar tus propios canales en la pestaña «Mis canales». Si eres quien instaló StockAI, agrega tu correo a `INSTANCE_ADMIN_EMAILS` y reinicia el servidor.' },
      ],
    },
  },

  en: {
    'solucion-de-problemas/archivo-rechazado': {
      title: 'StockAI will not accept my file',
      nav: 'File rejected',
      description:
        'What each notice means when you upload sales or inventory, why it appears, and what to change in the file so it goes in.',
      blocks: [
        { t: 'p', text: 'Almost every rejection is fixed in the file itself. Find the message you see below; each one says what it means and what to do.' },

        { t: 'h2', id: 'unsupported-format', text: 'Unsupported format' },
        { t: 'p', text: '**What you see:** "We cannot read .pdf files. Upload one of these formats: …" (with your file\'s extension).' },
        { t: 'p', text: '**Why:** sales are accepted as CSV, Excel (`.xlsx`, `.xls`), Parquet and JSON. A PDF, an image or a compressed file cannot be read as a table.' },
        { t: 'p', text: '**What to do:** export the report from your system as CSV or Excel. If you do not know where to start, download the [template](/plantilla_stockai.csv): its columns are `sku`, `fecha` (date), `demanda` (demand), `tienda`, `inventario`, `costo` and `precio`, and only the first three are required.' },

        { t: 'h2', id: 'too-large', text: 'The file is too large' },
        { t: 'p', text: '**What you see:** "The file is … MB and the limit is … MB. Upload a smaller one or split it up."' },
        { t: 'p', text: '**Why:** on the free plan each file can be up to 25 MB; on the paid plan, up to 2000 MB. See [Free plan limits](/docs/administracion/limites-del-plan).' },
        { t: 'p', text: '**What to do:** drop columns StockAI does not use (long descriptions, addresses, notes), save it as CSV instead of Excel, which is lighter, or upload it in parts. If your history genuinely does not fit, write to us from the notice.' },

        { t: 'h2', id: 'empty-file', text: 'The file is empty' },
        { t: 'p', text: '**What you see:** "The file for "…" is empty or has no columns. Upload a CSV with a header row and data." In the inventory importer: "We could not use a single row of the file. Check that it has a product-code column."' },
        { t: 'p', text: '**Why:** the file has no header row, no data rows, or no row carries the product code.' },
        { t: 'p', text: '**What to do:** open it and check that the first row holds the column names and that there is data below. If your system exports a title or a logo above the table, delete it.' },

        { t: 'h2', id: 'unreadable', text: 'It cannot be read' },
        { t: 'p', text: '**What you see:** "We could not read the file you uploaded. Check that it is a valid CSV or Excel file and try again." In inventory: "We could not open the file as a spreadsheet. Export it again as .xlsx or .csv."' },
        { t: 'p', text: '**Why:** the file is damaged, was cut off while downloading, or has an extension that does not match its content (for example, an HTML file renamed to `.xls`, which is how some systems "export to Excel").' },
        { t: 'p', text: '**What to do:** export it again from your system, open it in your spreadsheet to confirm it looks right, and save it again as `.xlsx` or `.csv`.' },

        { t: 'h2', id: 'nul-byte', text: 'The file contains a NUL byte' },
        { t: 'p', text: '**What you see:** a notice that the file could not be read, with the technical detail: "The file contains a NUL byte (first seen on line N). Rows containing one cannot be imported, because the value would be silently truncated. Re-export the file from your system."' },
        { t: 'p', text: '**Why:** a NUL byte is an invisible character (code zero) that should not be in a text file. It usually appears when a system exports in an unusual encoding, when the file was left half-written, or when it was copied from a database that stores fixed-length fields. If StockAI let it through, that cell\'s value would be cut short without warning, so it refuses instead. The line number tells you where the first one is.' },
        { t: 'p', text: '**What to do:** export the file again from your system, preferably as UTF-8 CSV. If the problem persists, open it in your spreadsheet and "Save as" CSV: that drops invisible characters. The inventory importer does not reject the whole file: rows with a NUL byte are skipped and counted among those left out.' },

        { t: 'h2', id: 'ambiguous-number', text: '"1.250": one thousand two hundred fifty, or one point two five?' },
        { t: 'p', text: '**What you see:** in the inventory importer, the question "Your file writes numbers like 1.250. How much is that?"; when uploading sales, the finding "We cannot tell whether the comma separates thousands or decimals".' },
        { t: 'p', text: '**Why:** in Latin America "1.250" usually means one thousand two hundred fifty; in a system set up in English, one point two five. If nothing in the file settles it, StockAI does not guess: a stock of 1250 read as 1.25 turns a full warehouse red.' },
        { t: 'p', text: '**What to do:** pick the option that matches your system. If you are unsure, look in the file for a number you know, such as the stock of a product you counted yesterday. If your integration uploads inventory through the API, the answer is a `422` with code `inventory_import_number_format_unclear`: "Your file writes numbers like …, and nothing in it says whether that is … or …. Upload it from Set up my inventory to choose, or fix it in your system." The integration can answer the question by stating the format in the call (see [the reference](/desarrolladores)).' },

        { t: 'h2', id: 'rows-left-out', text: 'Rows left out of the inventory' },
        { t: 'p', text: '**What you see:** in the importer\'s summary, "Rows with problems left out: …" and "Rows with no product code, skipped: …", with examples such as "Row 214 (SKU-001): "N/A"".' },
        { t: 'p', text: '**Why:** a row with no product code cannot be assigned to anything. A row with text where a number belongs ("N/A", "sold out"), with negative stock or cost, or with a NUL byte, is left out rather than filled with an invented value. If a product appears more than once, the last row wins.' },
        { t: 'p', text: '**What to do:** fix the rows the summary names and import again. If you already corrected some products by hand in StockAI, tick "Do not overwrite what I corrected by hand" so the file only fills what is missing.' },

        { t: 'h2', id: 'decide-first', text: 'There is something you have to decide first' },
        { t: 'p', text: '**What you see:** when confirming your sales columns, the block "There is something you have to decide first", with the continue button switched off. If you try to train anyway: "Before training you have to decide what to do about the problems we found in the file…".' },
        { t: 'p', text: '**Why:** the data check found something that would change the forecast without telling you, such as dates that could be day/month or month/day, or a comma that could separate thousands or decimals.' },
        { t: 'p', text: '**What to do:** answer each question. Every option says what it does and what it costs you if you get it wrong; the one marked recommended is only a suggestion. Once none is left unanswered, the button switches on by itself.' },
        { t: 'p', text: 'If the message is "This file cannot produce a forecast, and no correction would change that. Check the file and upload it again.", no question will fix it: it is usually a wrong column mapping (for example, the quantity column points at text). Fix the mapping or upload a different file.' },

        { t: 'h2', id: 'short-history', text: 'Products missing after training' },
        { t: 'p', text: '**What you see:** under Inventory, a notice that some uploaded products were left out of the forecast, with the reason "Only … rows of history (at least 20 are needed)".' },
        { t: 'p', text: '**Why:** a product needs at least **20 periods** of history to enter the forecast. With fewer, there is no way to measure whether a model gets it right, so it is left out rather than given an unsupported forecast.' },
        { t: 'p', text: '**What to do:** if the product is new, wait for more history. If you have older history in another file, upload it together. If you sell little each day, try a weekly or monthly detail level: see [Granularity](/docs/conceptos/granularidad). These products do not affect the rest of your recommendations.' },
      ],
    },

    'solucion-de-problemas/sin-datos': {
      title: 'A product says "No data"',
      nav: 'No data',
      description:
        '"No data" is not an error and not a safe product: it is a product missing something needed to compute its signal. Here is what is missing and how to supply it.',
      blocks: [
        { t: 'p', text: 'The [stock signal](/docs/conceptos/semaforo) needs two things from each product: a **forecast** (how much you will sell) and a **recorded stock** (how much you have). If either is missing, the product shows "No data" (stored as `SIN_DATOS`): no signal, no coverage and no suggested order quantity.' },
        { t: 'note', tone: 'warn', text: '"No data" means "we do not know", not "it is fine". A product with no recorded stock could be out of stock right now. That is why StockAI neither paints it green nor counts it as zero risk.' },

        { t: 'h2', id: 'no-stock', text: 'Its stock was never recorded' },
        { t: 'p', text: '**What you see:** "No data" in the signal and "No record" in the stock column.' },
        { t: 'p', text: '**Why:** StockAI does not invent zeros. An invented zero is indistinguishable from a counted one and would turn a full warehouse red, so until you say how much you hold, the product stays without a signal.' },
        { t: 'p', text: '**What to do:** load the stock under [Set up my inventory](app:/configurar-inventario), by uploading your system\'s file or by filling it in under "Start with these". You can also edit it on the product card under Inventory. See [Set up your inventory](/docs/primeros-pasos/configurar-inventario).' },

        { t: 'h2', id: 'no-stock-warehouse', text: 'It has no stock recorded in that warehouse' },
        { t: 'p', text: '**What you see:** when looking at one warehouse, "No data" with the note "Stock for this product was never recorded in this warehouse. That is not the same as zero: we do not know."' },
        { t: 'p', text: '**Why:** with several warehouses, stock is recorded per warehouse. If your file or system only carries the main warehouse\'s stock, the others have no figure. That used to be read as zero, and StockAI suggested restocking every branch while the goods sat in the main one; now it shows as what it is: a missing figure.' },
        { t: 'p', text: '**What to do:** record that product\'s stock in that warehouse, even if it is zero when it really is empty. See [Warehouses and transfers](/docs/uso-diario/bodegas-y-traslados).' },

        { t: 'h2', id: 'no-forecast', text: 'It has no forecast' },
        { t: 'p', text: '**What you see:** "No data" on a product that does have stock.' },
        { t: 'p', text: '**Why:** the product is not in the active run, for one of these reasons:' },
        { t: 'ul', items: [
          'It has fewer than **20 periods** of history and was left out before training. Inventory says so in the notice about products left out of the forecast, with the reason.',
          'It was not in the sales file the active run trained on (for example, a new product you added afterwards).',
          'A reliable forecast could not be produced for it.',
        ] },
        { t: 'p', text: '**What to do:** for a new product, include it in your next sales upload. If it lacks history, wait for it or try a weekly or monthly detail level ([Granularity](/docs/conceptos/granularidad)). If you believe it should be there, check under [History](/docs/analisis/historial) which file the active run used.' },

        { t: 'h2', id: 'estimated', text: 'The "estimated" label' },
        { t: 'p', text: 'This is not the same as "No data". A product with a signal can carry values marked **"estimated"**: they are StockAI\'s assumptions because nobody set them. The most common:' },
        { t: 'dl', items: [
          ['Lead time', '15 days, until you set it on the product or its supplier, and until StockAI learns it from 3 receptions. See [Learned lead time](/docs/conceptos/tiempo-de-entrega-aprendido).'],
          ['Service level', '95%, until you change it. See [Safety stock](/docs/conceptos/stock-de-seguridad).'],
          ['Minimum order (MOQ)', '1 unit.'],
        ] },
        { t: 'p', text: 'The signal is computed all the same, but on assumptions. A supplier who really takes 45 days will be flagged too late while StockAI keeps assuming 15. As soon as you record the real figure, the label disappears.' },

        { t: 'h2', id: 'no-cost', text: 'The value shows as "—"' },
        { t: 'p', text: 'When the **cost** is missing, StockAI cannot value the stock or the order, and shows "—" instead of zero. The product still has a signal; what is missing is its money value. Add the cost under Set up my inventory and the figures fill in.' },
      ],
    },

    'solucion-de-problemas/cuenta-y-acceso': {
      title: 'Account and access problems',
      nav: 'Account and access',
      description:
        'Unverified email, forgotten password, expired trial account, plan limit, permissions and other notices that stop you from doing something.',
      blocks: [
        { t: 'h2', id: 'email-not-verified', text: 'You have not verified your email yet' },
        { t: 'p', text: '**What you see:** an amber strip at the top of the app with "Resend email", or the message "You have not verified your email yet. Open the link we sent you to activate your account."' },
        { t: 'p', text: '**Why:** you can sign in, upload files, train and see the stock signal without verifying. What stays blocked are the actions that leave your company: **inviting people** and **sending orders to suppliers** by email or WhatsApp. An API key cannot send anything out either while no administrator has a verified email.' },
        { t: 'p', text: '**What to do:** open the link in the email we sent when you signed up. If you cannot find it, check your spam folder, then press "Resend email".' },

        { t: 'h2', id: 'link-expired', text: 'The verification link expired' },
        { t: 'p', text: '**What you see:** "This verification link is invalid or has expired. Request a new one below."' },
        { t: 'p', text: '**Why:** the link lasts 24 hours and works only once.' },
        { t: 'p', text: '**What to do:** on that same screen, type your email and ask for a new link.' },

        { t: 'h2', id: 'forgot-password', text: 'I forgot my password' },
        { t: 'steps', items: [
          'On the sign-in screen press "Forgot password?".',
          'Type your email. If an account exists, a 6-digit code arrives; the screen does not say whether the email exists, on purpose.',
          'Enter the code. It expires in 15 minutes and stops working after 5 wrong tries: ask for another.',
          'Set your new password: at least 8 characters, with at least one letter and one number.',
        ] },

        { t: 'h2', id: 'trial-expired', text: 'The trial account expired' },
        { t: 'p', text: '**What you see:** when signing in, "This trial account has ended. You can start a new one from the home page."' },
        { t: 'p', text: '**Why:** a trial account lasts 24 hours. When it ends it is erased, along with everything you uploaded.' },
        { t: 'p', text: '**What to do:** start another from the home page, or [create your account](/docs/primeros-pasos/crear-tu-cuenta) for free to keep your data. If you wrote to us from the trial account and we have not answered yet, we do not erase it until we do.' },
        { t: 'p', text: 'Other notices when asking for a trial account:' },
        { t: 'ul', items: [
          '"No trial accounts are available right now. Try again in a while." — too many are active at the moment.',
          '"You already created several trial accounts from this connection today. Use the one you have or come back tomorrow." — 3 are allowed per connection per day.',
        ] },

        { t: 'h2', id: 'plan-limit', text: 'You reached your free plan limit' },
        { t: 'p', text: '**What you see:** the dialog "You reached your free plan limit", or the message "You reached your free plan limit: … of …. Write to us to raise it."' },
        { t: 'p', text: '**Why:** the free plan has every feature, but ceilings on how much fits: 100 products, 2 users, 1 warehouse, 3 saved forecasts, 1 API key and files up to 25 MB. See [Free plan limits](/docs/administracion/limites-del-plan).' },
        { t: 'p', text: '**What to do:** free up room (for example, delete a saved forecast you no longer use under [History](/docs/analisis/historial)) or write to us from the same dialog, by WhatsApp, email or the form. There is nothing to buy on screen: raising the plan is a conversation.' },

        { t: 'h2', id: 'role', text: 'Your role cannot do this' },
        { t: 'p', text: '**What you see:** "Your role cannot do this. Ask an administrator at your company to do it, or to change your role."' },
        { t: 'p', text: '**Why:** with the **read-only** role you see everything but change nothing: no uploads, no training, no stock edits, no orders logged. An **analyst** does all of that but does not manage users. See [Users and roles](/docs/administracion/usuarios-y-roles).' },
        { t: 'p', text: '**What to do:** ask an administrator at your company to do it, or to change your role under Users.' },

        { t: 'h2', id: 'too-many-attempts', text: 'Too many attempts' },
        { t: 'p', text: '**What you see:** "Too many attempts. Wait a few minutes and try again." In the assistant: "Too many requests — please wait a moment before sending another message."' },
        { t: 'p', text: '**Why:** StockAI limits repeated sign-in and code attempts so nobody can guess a password. The assistant takes 20 messages per minute for the whole company.' },
        { t: 'p', text: '**What to do:** wait a few minutes and try again.' },

        { t: 'h2', id: 'assistant-unavailable', text: 'The assistant is unavailable' },
        { t: 'p', text: '**What you see:** in the assistant, "This installation has no language model configured, so the assistant cannot answer…".' },
        { t: 'p', text: '**Why:** the assistant needs a DeepSeek key on the installation, and this one has none. The rest of StockAI — forecasts, the stock signal and purchase orders — does not depend on it and works as usual.' },
        { t: 'p', text: '**What to do:** whoever administers the server turns it on under [Installation](/docs/administracion/instalacion), on the assistant\'s card.' },

        { t: 'h2', id: 'not-operator', text: 'This account does not operate the installation' },
        { t: 'p', text: '**What you see:** under Installation, on the "This installation" tab, the notice "This account does not operate the installation".' },
        { t: 'p', text: '**Why:** the server\'s service configuration (email, WhatsApp, assistant) belongs to whoever administers the deployment, not to a company\'s admin role. Those people are named in the server\'s `INSTANCE_ADMIN_EMAILS` variable.' },
        { t: 'p', text: '**What to do:** as your company\'s administrator you can set up your own channels on the "My channels" tab. If you are the one who installed StockAI, add your email to `INSTANCE_ADMIN_EMAILS` and restart the server.' },
      ],
    },
  },
}
