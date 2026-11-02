# Cómo embeber el asistente en Moodle

El asistente es un sitio estático (`frontend/widget/`) que se muestra en un `<iframe>` dentro del aula. No usa plugins, la API ni ninguna versión particular de Moodle (RNF08). Alcanza con pegar un fragmento HTML en un bloque o en una sección del curso.

En los ejemplos, `https://WIDGET` es la URL del proyecto `widget` en Vercel (por ejemplo, `https://asistente-appminds.vercel.app`). Reemplazala por la real.

## Antes de empezar: configuración del widget

Para que el campus pueda mostrar el widget y para que el modo flotante pueda cambiar de tamaño, el dominio del campus tiene que estar habilitado en dos lugares:

1. **`frontend/widget/vercel.json`**, en el encabezado `Content-Security-Policy: frame-ancestors …`. Hay que reemplazar `https://campus.example.edu.ar` por el dominio real del campus. Si el dominio no está ahí, el navegador no muestra el iframe.
2. **Variable `VITE_ORIGENES_PADRE`** del proyecto `widget` en Vercel. Es una lista separada por comas con el origen exacto del campus (`https://campus.ejemplo.edu.ar`, sin barra final) y el de moodle-sim. Solo hace falta para la variante A: si el origen no está en la lista, el widget no avisa los cambios de tamaño y el chat no se puede abrir.

## Variante A: flotante con script (recomendada)

Muestra el lanzador abajo a la derecha, como en el diseño. Se pega en un bloque "Texto" o "HTML" del curso:

```html
<script src="https://WIDGET/embed.js" data-offset="80" defer></script>
```

Qué hace `embed.js`:

- Crea un `<iframe>` con `position: fixed` abajo a la derecha, sin borde, con fondo transparente y `z-index` alto.
- Con el chat cerrado, el iframe mide solo lo que ocupan el lanzador y el globo de saludo (unos 350 × 100 px). El resto de la página sigue recibiendo clics.
- Al abrir el chat, el iframe se agranda a 450 × 680 px y se achica si la ventana es más chica. En celular (hasta 480 px de ancho) ocupa toda la pantalla mientras el chat está abierto.
- El widget le avisa al script los cambios con `postMessage`. Los mensajes llevan **solo el estado (abierto o cerrado) y las medidas**, nunca texto de la conversación. El script acepta mensajes solo del origen y de la ventana del widget. El widget, a su vez, solo los envía si el origen de la página está en `VITE_ORIGENES_PADRE`.

Atributos opcionales:

| Atributo | Por defecto | Para qué |
|---|---|---|
| `data-offset` | `80` | Distancia en px desde el borde inferior. Deja libre el botón "?" de ayuda de Moodle, que está abajo a la derecha. |
| `data-zindex` | `2147483000` | Si algún elemento del tema queda por encima del lanzador. |

El lanzador queda en la misma columna que el botón "?" de Moodle, justo encima. Se calcula para el "?" del tema Boost, que está a 2rem del borde y mide 36 px. Si en el campus real el "?" está en otro lugar, se ajusta con el token `--lanzador-derecha` de `frontend/widget/src/estilos/tokens.css`.

## Variante B: flotante sin script

Sirve si Moodle elimina el `<script>` pero deja pasar el `<iframe>`:

```html
<iframe
  src="https://WIDGET/?modo=flotante"
  title="Asistente virtual"
  style="position: fixed; right: 0; bottom: 80px; width: 450px; height: 680px;
         max-width: 100vw; max-height: calc(100vh - 80px);
         border: 0; background: transparent; color-scheme: light; z-index: 2147483000;"
></iframe>
```

Limitaciones:

- **El área transparente bloquea los clics.** El iframe ocupa siempre 450 × 680 px, aunque el chat esté cerrado. Los enlaces y botones de Moodle que queden detrás, abajo a la derecha, no se pueden usar.
- No cambia de tamaño ni pasa a pantalla completa en celular. En pantallas chicas el panel queda limitado al tamaño del iframe.
- Usala solo si la variante A no funciona. En ese caso conviene probar también la variante C.

## Variante C: incrustado en un bloque o sección

No tiene lanzador: el panel está siempre abierto y ocupa el 100 % del iframe. Es para un bloque lateral o una sección del curso:

```html
<iframe
  src="https://WIDGET/?modo=incrustado"
  title="Asistente virtual"
  style="width: 100%; min-width: 300px; height: 600px; border: 0;"
  loading="lazy"
></iframe>
```

- El ancho mínimo recomendado es de unos 300 px, y por debajo de 280 px se corta. La altura se puede ajustar, pero conviene que no baje de 480 px.
- Al cargar la página no le roba el foco a Moodle.

## Riesgo: Moodle puede limpiar el código

Moodle filtra el HTML que pegan los docentes. Según la configuración del sitio, puede **eliminar `<script>`, `<iframe>` o el atributo `style`** al guardar:

- Solo se conserva el código si el contenido es "confiable": el sitio tiene habilitado *Permitir contenido confiable* (`enabletrusttext`) y quien lo pega tiene el permiso `moodle/site:trustcontent`. Normalmente eso queda reservado a administradores.
- Si no, el filtro HTML de Moodle borra `<script>` siempre y suele borrar `<iframe>` o restringirlo a ciertos dominios.
- El editor (TinyMCE o Atto) también puede modificar el fragmento, así que hay que pegarlo en modo código HTML.

**Hay que probarlo en un aula real** antes de elegir la variante. Orden sugerido:

1. Pegar la variante A, guardar y volver a abrir la edición para ver si el `<script>` sigue ahí.
2. Si el `<script>` desaparece, probar la variante C en un bloque o sección y, si hace falta lanzador, la B.
3. Si también se borra el `<iframe>`, hay que pedirle a la administración del campus que habilite el dominio del widget o el contenido confiable para quien carga el bloque.

Antes de probar en el campus real se puede ensayar en `frontend/moodle-sim/`, que reproduce las tres variantes.

## Privacidad

- El widget no usa cookies (`credentials: 'omit'`), no tiene analytics y no lee nada de la página de Moodle.
- El identificador de sesión es un UUID aleatorio guardado en `sessionStorage`, o en memoria si el navegador bloquea el almacenamiento dentro del iframe.
- El texto de las consultas solo viaja al backend del asistente.
