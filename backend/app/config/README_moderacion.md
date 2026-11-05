# Catálogo local T47

Editar `moderacion.json` en UTF-8 y reiniciar el backend. El catálogo y sus
patrones se cachean por proceso; no hay recarga automática.

- `insultos`: palabras para ataques dirigidos, como `sos un …` o `qué … que sos`.
  Agregar `"pavote"` permite detectar `sos un pavote` sin editar Python.
- `insultos_aislados`: palabras que también bloquean como enunciado completo.
  Deben figurar en `insultos`. `boludo` y `sorete` quedan solo como dirigidos.
- `groserias_contextuales`: palabras para construcciones agresivas, como
  `andate al …`; su sola aparición no bloquea.
- `amenazas_frases`: amenazas literales. Agregar `"te voy a secuestrar"`
  permite detectarla sin editar Python. De `te voy a …` con verbo en infinitivo
  se derivan formas simples como `voy a secuestrarte` y
  `te voy a encontrar y secuestrar`.
- `discriminacion_frases`: calificativos o predicados degradantes, colocados
  después de un grupo dentro de una construcción dirigida o colectiva.
- `incitaciones_discriminatorias`: frases previas al artículo y grupo, como
  `hay que exterminar a`.
- `terminos_grupo`: bases singulares; no bloquean por sí mismas.

Se generan plurales regulares y femenino de bases terminadas en `o`. Agregar
otras formas explícitamente: `soreta`, por ejemplo. No se deriva `salama`.
Las cuatro listas de palabras no admiten espacios; las otras tres admiten frases.

Son obligatorias las siete listas, no vacías, con strings alfabéticos no vacíos.
Se normalizan tildes, mayúsculas y espacios, y se eliminan duplicados. El
subconjunto de aislados se comprueba después de normalizar. No agregar regex,
código, puntuación, secretos, datos personales ni instrucciones de flujo.

Ante archivo faltante/ilegible, UTF-8/JSON inválido o cualquier dato inválido,
se descarta **todo** el catálogo y se usa el respaldo mínimo interno. No se
mezclan entradas ni se registran términos, categorías o contenido de consultas.
El respaldo cubre insultos básicos (`idiota`, `imbécil`, `pelotudo`), la grosería
contextual `mierda`, `te voy a matar` y combinaciones discriminatorias mínimas
con `negro`/`inmigrante`. No reproduce todas las ampliaciones de este archivo.
Corregir el archivo y reiniciar también es necesario después de usar respaldo.

Seguridad, límites de palabra, negaciones y citas explicativas permanecen en
Python. Las guardas de seguridad funcionan aunque el catálogo sea inválido
y tienen prioridad sobre las citas. No se modifican T46 ni los estados HTTP.
