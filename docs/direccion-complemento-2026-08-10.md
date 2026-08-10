# Faro complementa, no reemplaza

**Creado:** 2026-08-10. Es un documento de **dirección**, no un plan ni una lista
de tareas. Sirve para decidir qué **no** construir.

## La regla

> Faro es dueño de las decisiones y de lo que aprende.
> El sistema del cliente es dueño del inventario, el catálogo y los proveedores.

Todo lo demás sale de ahí.

## Por qué ahora

El dueño lo planteó así: que Faro no sea "el software que usan siempre las
empresas" tipo SAP, sino algo que **complemente** lo que ya tienen. La razón
para escribirlo es que el código ya venía derivando hacia lo contrario.

Hoy Faro guarda stock por bodega, bodegas, transferencias, mermas, proveedores,
órdenes de compra, recepciones. Son tablas de ERP, y cada una tiene un dueño
anterior en la empresa del cliente. Tenerlas no es el problema; el problema es
que Faro pide ser **la segunda fuente de verdad sin tener cómo mantenerse
sincronizada**. Medido en la caminata del 2026-08-10:

- `/configurar-inventario` pide teclear stock, costo y días de entrega producto
  por producto — datos que el sistema del cliente ya tiene.
- El semáforo de esa sesión se calculaba sobre ventas de **hace 407 días**. La
  pantalla lo dice con honestidad, pero la única salida que ofrece es volver a
  subir un archivo a mano.
- Recepciones a mano. Importación de stock por CSV manual.

Ser sistema de registro obliga a todo lo demás: concurrencia, auditoría,
correcciones, migraciones, permisos finos. Ese es el camino a parecerse a SAP,
compitiendo además contra algo que el cliente no puede quitar.

## Las cuatro consecuencias de la regla

1. **Nunca pedirle a una persona que teclee lo que una máquina ya sabe.**
2. Los datos del cliente viven en Faro como **caché con procedencia y fecha**,
   no como registro.
3. Lo único propio de Faro es lo que nadie más guarda: **lead times aprendidos,
   niveles de servicio, y el historial de decisiones con su resultado.**
4. El valor llega **sin abrir la app**. El mensaje diario es el producto; la app
   es adonde vas cuando querés discutirle.

El punto 3 es el foso. Ningún ERP dice "este proveedor promete 7 días y entrega
en 11". Faro ya lo calcula.

## Los clientes usan "de todo" — y eso decide la forma

Preguntado por qué sistemas usan sus clientes reales, el dueño respondió: **de
todo**. Eso descarta la respuesta glamorosa. No se puede integrar con N sistemas
heterogéneos de SMB latinoamericanos; varios no tienen API utilizable y algunos
son Excel.

Pero **todos saben exportar un archivo**. El denominador común no es una API: es
el export. Entonces la versión honesta de "complementar" en este mercado es
**archivo-first, no API-first.**

Y esa es la buena noticia: **la parte difícil ya está construida.** El producto
ya detecta columnas por alias en español, ya sobrevive a cp1252 con acentos, ya
resuelve fechas dd/mm/yyyy, ya lee separadores `;` y números con coma, y ya
tiene una compuerta de datos con remediaciones. Eso es el diferenciador, y está
hecho.

**Lo que falta no es capacidad de lectura: es que la lectura sea un ciclo y no un
evento.** Hoy importar un archivo es un asistente que se corre una vez. La misma
fuente, con el mismo mapeo, reimportable de un clic y programable, es lo que
convierte "subir ventas" de tarea mensual en algo que pasa solo — y es lo que
ataca los 407 días. Las piezas existen sueltas: `/archivos` tiene fuentes,
`/ventas` recuerda el mapeo, `/automatizacion` tiene programaciones.

### Corrección al caminar `/integraciones` (mismo día)

Escrito lo anterior, resultó que **el ciclo automático ya existe — pero solo por
la vía API**. Hay conectores reales a Alegra y Siigo (`backend/integrations/`,
con registro, DTOs, cifrado de credenciales y suite de tests), y
`run_daily_integration_syncs` corre desde el bucle diario del worker, trayendo
catálogo, stock y ventas sin que nadie abra nada. Es exactamente la forma que
este documento pide. Nada de eso está sin construir.

Eso no invalida la tesis; la afila, y en tres puntos:

1. **El patrón ya está probado en casa.** No hay que inventar cómo se ve "que
   entre solo": ya se ve así para dos proveedores. Lo que falta es darle ese
   mismo tratamiento al **archivo**, que es por donde va a entrar el volumen.
2. **Dos conectores no cubren "de todo".** Cubren una tajada. Y la vía archivo
   sigue siendo la única que le sirve a todos los demás.
3. **Están detrás del plan Enterprise.** Es decir: la capacidad que hace que
   Faro complemente en vez de reemplazar es hoy la más cara del catálogo,
   mientras que teclear el inventario a mano está disponible para todos. Vale la
   pena revisar si ese es el reparto que se quiere.

Además, Alegra y Siigo son software contable **colombiano**, y el mercado ancla
declarado es **Costa Rica** (ver la memoria de estrategia, que ya marcaba esta
desalineación el 2026-07-19). Sigue sin definirse el equivalente real para CR.

## Qué sí, por orden de rendimiento

1. **Que la carga de archivo sea un ciclo.** Sobre todo cableado de piezas que
   ya existen.
2. **Procedencia y fecha en todo número que mueva plata.** Ya está a medias
   (badges de "estimado", "con un costo estimado", "no sabemos" en vez de "no
   hay riesgo") y es lo mejor del producto. Un ERP da un número; Faro dice de
   dónde salió y si se lo inventó.
3. **Que la decisión salga del edificio**: la OC en el formato del cliente, el
   mensaje al proveedor, el resumen diario. Existe parcialmente y está
   sub-invertido frente a la parte de llevar registro.

## Qué no

- **Congelar la superficie de ERP**, no borrarla: órdenes manuales,
  administración de bodegas, transferencias, mermas. Que no crezcan.
- **No construir una plataforma de integraciones.** Es la trampa natural al
  comprar esta tesis y falla justamente por el "de todo".
- **No sobrecorregir**: no se puede dejar de guardar stock, porque sin
  existencias no hay semáforo. La regla no es "no guardar", es **"no ser la
  autoridad"** — siempre mostrar de dónde vino cada dato y de cuándo es, y que
  refrescarlo sea trivial.

## Cómo se usa este documento

Ante una función nueva, la pregunta es: *¿esto guarda algo que el cliente ya
tiene en otro lado?* Si la respuesta es sí, casi siempre la respuesta correcta
es leerlo de su sistema o no hacerlo — no guardarlo mejor.
