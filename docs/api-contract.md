# Contrato entre el widget y la API

El widget usa el contrato **que ya existe en el backend**. Fuente de verdad: `backend/app/models/chat.py`, `backend/app/routers/chat.py` y `backend/app/services/limite_solicitudes.py`. Los tipos del front están en `frontend/widget/src/api/tipos.ts`, y si el backend cambia hay que actualizar los dos lugares.

## `POST /chat`

### Encabezados

| Encabezado | Valor |
|---|---|
| `Content-Type` | `application/json` |
| `X-Sesion-Id` | UUID v4 en minúsculas, generado al azar por el widget al abrir la página. Se guarda en `sessionStorage` o, si el iframe no lo permite, en memoria. No se deriva de datos de la persona (RNF01). |

El widget envía la solicitud con `credentials: 'omit'`: no manda cookies.

### Cuerpo

```json
{
  "mensaje": "¿Cuándo es el primer parcial?",
  "contexto": {
    "materia_id": 2,
    "comision": "A",
    "periodo": null,
    "consulta_pendiente": null
  }
}
```

- `mensaje`: entre 1 y 2000 caracteres.
- `contexto`: el widget reenvía **tal cual** el `contexto` que vino en la respuesta anterior. Cuando la persona elige una materia o una comisión en el selector (RF03), se completa `materia_id` o `comision` y se mantiene `consulta_pendiente`.
- El modelo es `extra="forbid"` y `strict=True`, así que el widget **no** envía campos extra (ni `categoria` ni `idSesion`).

### Respuesta 200

```json
{
  "estado": "respondida",
  "respuesta": "Texto de la respuesta…",
  "fuentes": [
    { "documento_id": 1, "drive_id": "…", "titulo": "Plan de estudio", "enlace": "https://…", "fragmento_ids": [10] }
  ],
  "confianza": 0.82,
  "umbral": 0.6,
  "advertencia_temporal": "Verificá la información vigente en el canal institucional oficial del IFTS 29.",
  "contexto": { "materia_id": 2, "comision": "A", "periodo": null, "consulta_pendiente": null },
  "campo_requerido": null,
  "derivacion": null,
  "correo_prearmado": null
}
```

`derivacion` y `correo_prearmado` siempre vienen en la respuesta. Valen `null` salvo cuando `estado = "derivada"`, y en ese caso los dos son obligatorios.

#### Respuesta derivada

Cuando no hay evidencia suficiente pero la consulta coincide con un sector de `backend/app/config/derivaciones.json` (por nombre o palabra clave), el backend responde:

```json
{
  "estado": "derivada",
  "respuesta": "No cuento con evidencia suficiente para responder esa consulta. Podés consultar a Secretaría Académica.",
  "fuentes": [],
  "confianza": 0.0,
  "umbral": 0.7,
  "advertencia_temporal": null,
  "contexto": { "materia_id": null, "comision": null, "periodo": null, "consulta_pendiente": null },
  "campo_requerido": null,
  "derivacion": {
    "sector": "Secretaría Académica",
    "correo": "secretaria@ifts29.edu.ar",
    "horario": "Martes y Jueves de 19:00 a 21:00",
    "responsable": "Secretaría"
  },
  "correo_prearmado": {
    "destinatario": "secretaria@ifts29.edu.ar",
    "asunto": "Consulta de estudiante - IFTS 29",
    "cuerpo": "Hola,\n\nQuisiera realizar una consulta relacionada con gestiones estudiantiles del IFTS 29.\n\nMuchas gracias."
  }
}
```

- `responsable` puede ser `null`.
- `correo_prearmado.destinatario` siempre coincide con `derivacion.correo`.
- La plantilla del correo es fija y no incluye el texto de la consulta (RNF01).

### Cómo muestra el widget cada estado

| `estado` | Interfaz |
|---|---|
| `respondida` | Burbuja con `respuesta` y una tarjeta por cada elemento de `fuentes` (título y "Descargar" si hay `enlace`), según RF02. |
| `requiere_contexto` | Burbuja con la pregunta y selector de materia (`campo_requerido = "materia_id"`) o de comisión (`"comision"`), según RF03. |
| `derivada` | Burbuja con `respuesta` y bloque de derivación (RF05) con los datos de `derivacion`: sector, contacto (si viene `responsable`), correo y horario. El botón "Escribir a {sector}" abre un `mailto:` armado con `correo_prearmado`, y lo envía el propio estudiante. |
| `sin_evidencia` | Burbuja con `respuesta` y el mismo bloque de derivación, pero con un sector de respaldo: el backend no eligió ninguno. **Esos datos son estáticos del front** (`src/config/derivaciones.ts`, Bedelía) y el `mailto:` lo arma el widget, con la consulta recortada a 500 caracteres. |
| `bloqueada` | Advertencia de netiqueta diferenciada (RF10). |
| cualquiera con `advertencia_temporal` | Recordatorio de verificación debajo de la respuesta (RF07). |

No se usan `confianza` ni `umbral` en la interfaz.

### Errores

| Código | Origen | Interfaz |
|---|---|---|
| 400 | Falta `X-Sesion-Id` o no es un UUID | "No se pudo identificar la sesión…" |
| 422 | Validación del cuerpo | "No se pudo enviar la consulta…" |
| 429 | Límite por sesión. Trae `Retry-After` | Mensaje del backend (`detail`) y reintento habilitado después de la espera |
| 503 | `detail: { codigo, mensaje }` | `detail.mensaje` y botón "Reintentar" |
| Red o CORS | — | "No hay conexión con el asistente…" y "Reintentar" |
| Sin respuesta en 4 s | Arranque en frío (RNF04) | "El asistente se está iniciando, puede tardar unos segundos…" sin cancelar |
| Sin respuesta en 45 s | — | "El asistente está tardando demasiado…" y "Reintentar" |

## Cliente mock

Si `VITE_API_URL` está vacía, el widget usa `src/api/clienteMock.ts`, que imita el flujo de `conversacion.py` con latencia artificial. Para forzar estados a mano, escribí en el chat:

- `simular error`: 503.
- `simular limite`: 429 con espera de 30 s.
- `simular arranque`: demora de 9 s (arranque en frío).
- Un insulto (por ejemplo, "idiota"): `bloqueada`.
- `parcial`, `cronograma`, `examen` o `Mi cursada`: pide materia y comisión.
- `plan de estudio`: respuesta con fuente PDF.
- Una palabra clave de `derivaciones.json` (por ejemplo "equivalencias", "título" o "pase" para Secretaría Académica, o "correlativas" o "certificado de alumno regular" para Bedelía): `derivada`, con el mismo correo prearmado que el backend.
- Cualquier otra cosa: `sin_evidencia`.

## Pendiente del lado del backend

1. **CORS (resuelto, falta configurarlo en Vercel).** `backend/app/main.py` registra `CORSMiddleware` con los orígenes de la variable `CORS_ORIGENES` (lista separada por comas; por defecto `http://localhost:5173`). Permite `POST` con `Content-Type` y `X-Sesion-Id`, y expone `Retry-After` para que el widget lea la espera del 429. En el proyecto del backend en Vercel hay que cargar `CORS_ORIGENES` con la URL real del widget (por ejemplo `https://<widget>.vercel.app`, sin barra final). Si no se carga, el navegador bloquea el cliente real en producción.

2. **Ids de materias.** El front asume `1 Técnicas de Programación`, `2 Administración de Base de Datos`, `3 Elementos de Análisis Matemático` y `4 Lógica Computacional`, según el orden del `INSERT` de `0001_esquema_inicial.sql`. Habría que confirmarlo o exponer `GET /materias`.

## Extensiones propuestas (opcionales, a decidir)

Ninguna es necesaria para que el widget funcione. Si se agregan, el front las aprovecha sin romper nada:

| Propuesta | Para qué |
|---|---|
| `categoria?: str` en `SolicitudChat` (slugs `primeros-pasos-moodle`, `mi-cursada`, `asistencia-entregas`, `plan-de-estudios`, `tramites-siu`, `ayuda-tecnica`) | Que el chip de tema llegue como dato y no solo como texto, alineado con el campo `categoria` del modelo de datos. |
| `GET /materias` y comisiones por materia | Reemplazar las listas de ejemplo del front (`src/config/materias.ts` y `comisiones.ts`). |
| `POST /encuesta` con `{ puntaje: 1..5, comentario? }` | Hoy la encuesta (RF11) queda solo en el navegador y no se envía. |
