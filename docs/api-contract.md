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
  "campo_requerido": null
}
```

### Cómo muestra el widget cada estado

| `estado` | Interfaz |
|---|---|
| `respondida` | Burbuja con `respuesta` y una tarjeta por cada elemento de `fuentes` (título y "Descargar" si hay `enlace`), según RF02. |
| `requiere_contexto` | Burbuja con la pregunta y selector de materia (`campo_requerido = "materia_id"`) o de comisión (`"comision"`), según RF03. |
| `sin_evidencia` | Burbuja con `respuesta` y bloque de derivación (RF05) con sector, correo, horario y un botón `mailto:` prearmado que envía el propio estudiante. **Los datos del sector son estáticos del front** (`src/config/derivaciones.ts`, copiados de `backend/app/config/derivaciones.json`), porque la respuesta no los trae. |
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
- Cualquier otra cosa: `sin_evidencia`.

## Pendiente del lado del backend

1. **CORS (bloqueante para el cliente real).** `backend/app/main.py` no tiene `CORSMiddleware`. El widget se sirve desde otro origen, así que el navegador va a bloquear la llamada hasta que se agregue algo como esto:

   ```python
   from fastapi.middleware.cors import CORSMiddleware

   app.add_middleware(
       CORSMiddleware,
       allow_origins=["https://<widget>.vercel.app", "http://localhost:5173"],
       allow_methods=["POST"],
       allow_headers=["Content-Type", "X-Sesion-Id"],
       expose_headers=["Retry-After"],
       allow_credentials=False,
   )
   ```

   `expose_headers=["Retry-After"]` hace falta para que el widget pueda leer la espera del 429.

2. **Ids de materias.** El front asume `1 Técnicas de Programación`, `2 Administración de Base de Datos`, `3 Elementos de Análisis Matemático` y `4 Lógica Computacional`, según el orden del `INSERT` de `0001_esquema_inicial.sql`. Habría que confirmarlo o exponer `GET /materias`.

## Extensiones propuestas (opcionales, a decidir)

Ninguna es necesaria para que el widget funcione. Si se agregan, el front las aprovecha sin romper nada:

| Propuesta | Para qué |
|---|---|
| `categoria?: str` en `SolicitudChat` (slugs `primeros-pasos-moodle`, `mi-cursada`, `asistencia-entregas`, `plan-de-estudios`, `tramites-siu`, `ayuda-tecnica`) | Que el chip de tema llegue como dato y no solo como texto, alineado con el campo `categoria` del modelo de datos. |
| `derivacion?: { sector, correo, horario }` en `RespuestaChat` cuando `estado = "sin_evidencia"` | Derivar al sector que corresponde según la consulta (hoy el front muestra siempre Bedelía). |
| `GET /materias` y comisiones por materia | Reemplazar las listas de ejemplo del front (`src/config/materias.ts` y `comisiones.ts`). |
| `POST /encuesta` con `{ puntaje: 1..5, comentario? }` | Hoy la encuesta (RF11) queda solo en el navegador y no se envía. |
