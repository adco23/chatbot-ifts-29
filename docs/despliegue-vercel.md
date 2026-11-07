# Guía de despliegue en Vercel

Esta guía explica cómo publicar el asistente completo en Vercel con el plan gratuito (Hobby), que no pide tarjeta. Está escrita para el equipo y sigue la configuración que ya tiene el repo.

## Qué se despliega

El repo tiene tres aplicaciones. Cada una es un **proyecto distinto de Vercel**, conectado al mismo repositorio de GitHub y con su propio **Root Directory**:

| Proyecto en Vercel | Root Directory | Qué es | Preset |
|---|---|---|---|
| `appminds-api` | `backend` | API FastAPI (`POST /chat`, `GET /health`, `/admin/sync`) | FastAPI |
| `appminds-widget` | `frontend/widget` | Widget React que se embebe en Moodle | Vite |
| `appminds-moodle-sim` | `frontend/moodle-sim` | Aula simulada para probar el embebido | Vite |

Los nombres son una sugerencia. Lo importante es que el nombre define la URL de producción: un proyecto llamado `appminds-api` queda en `https://appminds-api.vercel.app`, salvo que ese subdominio ya esté tomado. Si Vercel le asigna otro, la URL real se ve en **Settings → Domains**.

Servicios externos que usa el backend, todos con plan gratuito:

- **Supabase**: Postgres con pgvector (base de datos y búsqueda vectorial).
- **Google AI Studio**: clave de Gemini (modelo de lenguaje y embeddings).
- **GroqCloud**: clave de Groq (modelo de respaldo).
- **Google Cloud**: cuenta de servicio con acceso de solo lectura a la carpeta de Drive.
- **Cloudflare Workers AI** (opcional): respaldo de embeddings.

### Quién apunta a quién

Las tres aplicaciones se referencian entre sí, así que algunas URL hay que cargarlas después de que exista el proyecto al que apuntan:

| Dónde | Variable o archivo | Valor |
|---|---|---|
| API | `CORS_ORIGENES` | URL del widget |
| Widget | `VITE_API_URL` | URL de la API |
| Widget | `VITE_ORIGENES_PADRE` | URL de moodle-sim (y la del campus real) |
| Widget | `frontend/widget/vercel.json` → `frame-ancestors` | URL de moodle-sim (y la del campus real) |
| moodle-sim | `VITE_WIDGET_URL` | URL del widget |

Todas las URL van **con `https://` y sin barra final**.

El orden recomendado es: API → widget → moodle-sim → volver a desplegar la API y el widget con las URL definitivas. Si elegís los nombres de los proyectos de antemano, podés anotar las URL desde el principio y evitar la vuelta.

---

## Paso 0. Antes de empezar

### Cuentas

1. Cuenta de **Vercel** en el plan Hobby, iniciando sesión con GitHub. La cuenta tiene que tener acceso al repositorio. Si el repo es de una organización, alguien con permisos tiene que autorizar la app de Vercel en esa organización.
2. Cuenta de **Supabase** (plan Free).
3. Clave de **Gemini**: en Google AI Studio, sección *Get API key*.
4. Clave de **Groq**: en la consola de GroqCloud, sección *API Keys*.
5. Proyecto de **Google Cloud** para la cuenta de servicio de Drive (ver paso 2).

### Rama de producción

Vercel toma como producción la rama por defecto del repo (`main`). Hay tres cosas que **solo funcionan en producción**:

- La URL fija `https://<proyecto>.vercel.app`. Los deploys de otras ramas reciben URL distintas, que además piden iniciar sesión en Vercel (ver "Previews y protección" más abajo).
- La tarea programada de sincronización (cron).
- Las variables cargadas solo para el entorno *Production*.

Si el código que querés publicar todavía está en `feature/bloque-8`, hay dos opciones: integrarlo a `main` antes de desplegar (recomendado) o cambiar temporalmente la rama de producción en **Settings → Git → Production Branch** de cada proyecto.

### Cambios que hay que hacer en el repo antes del primer deploy

**1. Sacar las dependencias pesadas de `backend/requirements.txt` (bloqueante).**

El archivo incluye `langchain-huggingface` y `sentence-transformers`, que el código no importa en ningún lado. `sentence-transformers` instala PyTorch, que pesa varios GB. El límite de una función Python en Vercel es de **500 MB sin comprimir**, así que con esas dos líneas el deploy del backend casi seguro falla por tamaño.

El archivo también repite `fastapi` y `supabase` dos veces, una sin versión y otra con versión fija. Conviene dejar una sola línea con versión fija. Una versión limpia sería:

```text
# Framework web y servidor
fastapi==0.143.0
uvicorn==0.54.0
# Configuración y HTTP
pydantic-settings==2.15.0
httpx==0.28.1
# Base de datos
supabase==2.31.0
# Google Drive
google-api-python-client
google-auth-httplib2
google-auth-oauthlib
# Limpieza de texto
unidecode
thefuzz
# Extracción de PDF y Word
pypdf
python-docx
# Fragmentación de texto
langchain-text-splitters
```

Después de editarlo, probá en local que la API arranque y que pasen las pruebas (`pytest` desde `backend/`).

**2. Reemplazar los dominios de ejemplo en `frontend/widget/vercel.json` (bloqueante para el embebido).**

El encabezado `Content-Security-Policy` define qué sitios pueden mostrar el widget dentro de un iframe. Hoy tiene dominios de ejemplo:

```text
frame-ancestors 'self' https://campus.example.edu.ar https://moodle-sim.example.vercel.app
```

Hay que poner la URL real de moodle-sim y, cuando se sepa, la del campus. Por ejemplo:

```text
frame-ancestors 'self' https://appminds-moodle-sim.vercel.app https://campus.ejemplo.edu.ar
```

Si la URL de moodle-sim no está en esa lista, el navegador se niega a mostrar el iframe. Este archivo forma parte del deploy, así que el cambio tiene que estar commiteado en la rama de producción.

---

## Paso 1. Supabase

1. En el panel de Supabase, **New project**. Elegí una región cercana (por ejemplo, São Paulo) y guardá la contraseña de la base en un lugar seguro.
2. Abrí **SQL Editor** y aplicá las migraciones de `backend/migrations/` **en orden, una por una**. Ningún script las aplica solo: hay que pegar el contenido de cada archivo y ejecutarlo.
   - `0001` a `0007`: se aplican seguidas. La `0001` habilita la extensión `vector`.
   - **`0008` no se aplica todavía.** Primero hay que configurar `SUPABASE_BACKEND_KEY` en Vercel y comprobar que el backend funcione con esa clave (paso 3). El procedimiento completo está en [seguridad-supabase.md](seguridad-supabase.md).
   - `0009`: después de la `0008`.
3. En **Project Settings → API Keys** copiá:
   - la URL del proyecto (`https://<id>.supabase.co`, sin `/rest/v1`) → `SUPABASE_URL`;
   - la clave *publishable* → `SUPABASE_KEY`;
   - una clave *secret* (`sb_secret_...`) → `SUPABASE_BACKEND_KEY`.

La clave secreta saltea la seguridad por filas (RLS). Va **solo** en el proyecto de la API en Vercel. Nunca en el widget, en una variable `VITE_*`, en el repo ni en un mensaje de chat del equipo.

> El plan Free de Supabase pausa los proyectos que no tienen actividad durante una semana. Si la API empieza a fallar con errores de base después de un tiempo sin uso, entrá al panel y reanudá el proyecto.

---

## Paso 2. Cuenta de servicio de Google Drive

El backend lee los documentos institucionales de una carpeta de Drive con permisos de solo lectura.

1. En la consola de Google Cloud, creá un proyecto (o usá uno existente).
2. En **APIs y servicios → Biblioteca**, habilitá **Google Drive API**.
3. En **IAM y administración → Cuentas de servicio**, creá una cuenta de servicio. No necesita roles del proyecto.
4. Dentro de la cuenta, en **Claves → Agregar clave → Crear clave nueva → JSON**. Se descarga un archivo `.json`. No lo subas al repo: `.gitignore` ya excluye `credentials*.json` y `service-account*.json`, pero conviene guardarlo fuera de la carpeta del proyecto.
5. En Drive, compartí la carpeta de documentos con el correo de la cuenta de servicio (termina en `iam.gserviceaccount.com`), con permiso de **Lector**.
6. Anotá el identificador de la carpeta: es la parte final de su URL (`https://drive.google.com/drive/folders/<ID>`).

En Vercel se cargan así:

- `GOOGLE_SERVICE_ACCOUNT_JSON`: el **contenido completo** del archivo JSON, pegado tal cual.
- `GOOGLE_DRIVE_FOLDER_ID`: el identificador de la carpeta. Si falta, el código usa una carpeta fija que quedó en `backend/app/drive/cliente.py`. Conviene cargarla igual, para que quede explícito qué carpeta se indexa.

---

## Paso 3. Backend (`appminds-api`)

### Crear el proyecto

1. En Vercel, **Add New… → Project** e importá el repositorio.
2. **Project Name**: `appminds-api` (o el que elijan).
3. **Root Directory**: tocá *Edit* y elegí `backend`.
4. **Framework Preset**: tiene que aparecer **FastAPI**. Vercel lo detecta solo porque:
   - `backend/pyproject.toml` declara el punto de entrada (`[tool.vercel] entrypoint = "app.main:app"`);
   - la tabla `[project]` de `backend/pyproject.toml` incluye `fastapi`. Cuando hay un `pyproject.toml`, Vercel instala las dependencias desde ahí y **no lee `requirements.txt`**. Las dos listas tienen que coincidir;
   - `backend/.python-version` fija Python 3.12, que Vercel admite.
5. No hace falta tocar *Build Command* ni *Output Directory*.
6. Antes de tocar **Deploy**, cargá las variables de entorno (siguiente apartado).

### Variables de entorno

Se cargan en la pantalla de importación o, después, en **Settings → Environment Variables**. Marcá las claves como **Sensitive** para que no se puedan volver a leer desde el panel.

**Obligatorias para que el asistente responda:**

| Variable | Valor | Notas |
|---|---|---|
| `ENTORNO` | `produccion` | Así la API no busca un archivo `.env` y `/health` informa el entorno. |
| `GEMINI_API_KEY` | Clave de Google AI Studio | Proveedor principal y embeddings. |
| `GROQ_API_KEY` | Clave de GroqCloud | Respaldo del modelo de lenguaje. |
| `SUPABASE_URL` | `https://<id>.supabase.co` | Sin `/rest/v1`. |
| `SUPABASE_KEY` | Clave *publishable* | |
| `SUPABASE_BACKEND_KEY` | Clave `sb_secret_...` | Necesaria antes de aplicar la migración `0008`. |
| `CORS_ORIGENES` | `https://appminds-widget.vercel.app` | URL exacta del widget. No admite `*`. Si hay varios orígenes, van separados por coma. |

**Obligatorias para la sincronización con Drive:**

| Variable | Valor | Notas |
|---|---|---|
| `ADMIN_API_TOKEN` | Token aleatorio de 32 caracteres o más | Protege `/admin/sync`. Con menos de 16 caracteres, la ruta rechaza todo. |
| `CRON_SECRET` | **El mismo valor que `ADMIN_API_TOKEN`** | Vercel lo envía como `Authorization: Bearer <valor>` al ejecutar el cron. |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Contenido del JSON de la cuenta de servicio | |
| `GOOGLE_DRIVE_FOLDER_ID` | Identificador de la carpeta | |

Para generar el token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Opcionales.** Todas tienen valores por defecto razonables, documentados en `backend/.env.example`. Las más útiles:

| Variable | Para qué |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` y `CLOUDFLARE_API_TOKEN` | Respaldo de embeddings con Cloudflare Workers AI. |
| `VERSION_APP` | Versión que muestra `/health`. |
| `LIMITE_SOLICITUDES_MAXIMO` y `LIMITE_SOLICITUDES_VENTANA_SEGUNDOS` | Límite de consultas por sesión (por defecto, 10 por minuto). |
| `GEMINI_MODELO` y `GROQ_MODELO` | Cambiar de modelo sin tocar código. |

Cuidado con los valores inválidos: la configuración se valida al arrancar. Un proveedor desconocido en `LLM_ORDEN_PROVEEDORES` o `EMBEDDING_PRIMARY`, un `*` en `CORS_ORIGENES` o un origen sin `https://` hacen que la API no levante y responda 500 en todas las rutas. El motivo aparece en los logs de la función.

**Entornos de cada variable.** Vercel pregunta en qué entornos aplica cada variable (*Production*, *Preview*, *Development*). Para empezar alcanza con *Production*. Si cargás las claves también en *Preview*, los deploys de otras ramas van a usar la misma base y la misma cuota de los proveedores.

### Desplegar y verificar

Tocá **Deploy**. Cuando termine:

1. Abrí `https://appminds-api.vercel.app/health`. Tiene que responder algo así:

   ```json
   {"estado": "ok", "servicio": "asistente-ifts29-api", "version": "0.1.0", "entorno": "produccion"}
   ```

   Si dice `"entorno": "local"`, falta `ENTORNO=produccion`.

2. Abrí `https://appminds-api.vercel.app/docs` para ver la documentación interactiva de FastAPI.

3. Probá una consulta. En Git Bash, macOS o Linux:

   ```bash
   curl -X POST https://appminds-api.vercel.app/chat \
     -H "Content-Type: application/json" \
     -H "X-Sesion-Id: 3f1c9a52-7d4e-4b8a-9c21-5e6f7a8b9c0d" \
     -d '{"mensaje": "¿Cuándo empieza la cursada?", "contexto": {}}'
   ```

   En PowerShell:

   ```powershell
   Invoke-RestMethod -Method Post -Uri https://appminds-api.vercel.app/chat `
     -ContentType 'application/json; charset=utf-8' `
     -Headers @{ 'X-Sesion-Id' = '3f1c9a52-7d4e-4b8a-9c21-5e6f7a8b9c0d' } `
     -Body '{"mensaje": "¿Cuándo empieza la cursada?", "contexto": {}}'
   ```

   El formato completo del cuerpo y de la respuesta está en [api-contract.md](api-contract.md). Mientras la base esté vacía, lo esperable es que el asistente derive la consulta por falta de evidencia.

4. Si el deploy falla en el build, revisá el log. "Function size exceeded" o un mensaje parecido indica que `requirements.txt` todavía tiene las dependencias pesadas (ver paso 0). Para ver cuánto pesa cada función, agregá la variable `VERCEL_ANALYZE_BUILD_OUTPUT=1` y volvé a desplegar.

### Terminar la seguridad de Supabase

Con la API funcionando y `SUPABASE_BACKEND_KEY` cargada, aplicá `0008` y después `0009` desde el SQL Editor, siguiendo [seguridad-supabase.md](seguridad-supabase.md). Después de aplicarlas, volvé a probar `/health` y una consulta a `/chat`.

---

## Paso 4. Carga inicial de documentos

La tarea programada sincroniza Drive una vez por día, pero la primera carga conviene lanzarla a mano para no esperar.

Desde Git Bash, macOS o Linux:

```bash
curl -X POST https://appminds-api.vercel.app/admin/sync \
  -H "Authorization: Bearer <ADMIN_API_TOKEN>"
```

Desde PowerShell:

```powershell
Invoke-RestMethod -Method Post -Uri https://appminds-api.vercel.app/admin/sync `
  -Headers @{ Authorization = 'Bearer <ADMIN_API_TOKEN>' }
```

Qué significa cada respuesta:

| Código | Significado |
|---|---|
| 200 | Sincronización completa. |
| 207 | Sincronización parcial: algunos documentos fallaron. El detalle viene en la respuesta. |
| 401 | Token ausente, distinto o de menos de 16 caracteres. |
| 409 | Ya hay otra sincronización en curso. |
| 503 | Error general, por ejemplo sin acceso a Drive o a la base. |

En el plan Hobby una función puede correr hasta **300 segundos**. Si la carpeta tiene muchos documentos y la primera carga se corta, hay dos alternativas:

- Volver a llamar a `/admin/sync`. La sincronización es incremental y no reprocesa lo que ya quedó indexado.
- Correr la carga desde tu máquina con `python scripts/sincronizar_drive.py ejecutar`, desde `backend/` y con un `.env` que apunte a la base de producción. Esto escribe en la base real, así que coordinalo con el equipo antes.

Las preguntas frecuentes curadas se cargan aparte, también desde tu máquina:

```bash
cd backend
python scripts/cargar_faq.py cargar --archivo examples/faq_curadas.json
```

---

## Paso 5. Widget (`appminds-widget`)

1. **Add New… → Project**, el mismo repositorio.
2. **Project Name**: `appminds-widget`.
3. **Root Directory**: `frontend/widget`.
4. **Framework Preset**: Vite. `frontend/widget/vercel.json` ya fija `npm run build` como comando y `dist` como carpeta de salida.
5. Variables de entorno:

| Variable | Valor | Notas |
|---|---|---|
| `VITE_API_URL` | `https://appminds-api.vercel.app` | Sin barra final. **Vacía, el widget usa el cliente mock** y no llama al backend. |
| `VITE_ORIGENES_PADRE` | `https://appminds-moodle-sim.vercel.app` | Agregá el origen del campus real, separado por coma, cuando se sepa. Sin esto el modo flotante no se puede abrir. |
| `VITE_PREGUNTAS_SUGERIDAS` | `false` | `true` para mostrar las preguntas sugeridas (RF14). |

6. **Deploy**.

Hay que tener en cuenta dos cosas:

- **Las variables `VITE_*` se fijan al compilar.** Si cambiás alguna, el cambio no se aplica hasta el próximo deploy: en **Deployments**, menú `⋯` del último deploy → **Redeploy**.
- Estas variables terminan en el JavaScript público, así que nunca pongas secretos en ellas.

Verificación: abrí `https://appminds-widget.vercel.app/?modo=incrustado`, mandá una consulta y revisá en las herramientas de desarrollo del navegador (pestaña *Red*) que la solicitud vaya a la URL de la API y no haya errores de CORS. Si no ves ninguna solicitud de red, el widget se compiló con `VITE_API_URL` vacía y está usando el mock.

El script de embebido queda publicado en `https://appminds-widget.vercel.app/embed.js`. Cómo pegarlo en Moodle está en [embed.md](embed.md).

---

## Paso 6. Simulación de Moodle (`appminds-moodle-sim`)

1. **Add New… → Project**, el mismo repositorio.
2. **Project Name**: `appminds-moodle-sim`.
3. **Root Directory**: `frontend/moodle-sim`.
4. **Framework Preset**: Vite.
5. Variable de entorno:

| Variable | Valor |
|---|---|
| `VITE_WIDGET_URL` | `https://appminds-widget.vercel.app` |

6. **Deploy**.

`frontend/moodle-sim/vercel.json` ya agrega `X-Robots-Tag: noindex, nofollow`, para que los buscadores no indexen la simulación.

---

## Paso 7. Cerrar el circuito

Con los tres proyectos creados, revisá que las URL coincidan y volvé a desplegar lo que haga falta:

1. **API**: `CORS_ORIGENES` tiene que ser exactamente la URL del widget. Si la cambiaste, hacé *Redeploy* de la API, porque la configuración se lee una sola vez por instancia.
2. **Widget**: `VITE_API_URL` y `VITE_ORIGENES_PADRE` tienen que tener las URL reales. Además, el `frame-ancestors` de `frontend/widget/vercel.json` tiene que incluir la URL de moodle-sim (paso 0). Si cambiaste algo, hacé *Redeploy* del widget.
3. **moodle-sim**: `VITE_WIDGET_URL` tiene que ser la URL del widget.

Prueba de punta a punta en `https://appminds-moodle-sim.vercel.app`:

- [ ] Se ve el cartel de simulación.
- [ ] El lanzador aparece abajo a la derecha, encima del botón "?".
- [ ] Al tocarlo se abre el panel y el iframe se agranda.
- [ ] Una consulta devuelve una respuesta del backend, con documento fuente y enlace cuando hay evidencia.
- [ ] Una consulta sin evidencia muestra la derivación (sector, correo y horario).
- [ ] En celular (o con la vista responsiva del navegador, menos de 480 px de ancho) el chat abierto ocupa toda la pantalla.
- [ ] La consola del navegador no muestra errores de CORS ni de `frame-ancestors`.

---

## Paso 8. Tarea programada de sincronización

`backend/vercel.json` ya declara el cron:

```json
"crons": [{"path": "/admin/sync", "schedule": "0 6 * * *"}]
```

- Se ejecuta **una vez por día a las 6:00 UTC (3:00 en Argentina)**. En el plan Hobby la hora no es exacta: puede ejecutarse en cualquier momento entre las 6:00 y las 6:59 UTC.
- Hobby solo admite crons diarios. Una expresión más frecuente, como `0 * * * *`, hace fallar el deploy.
- **Solo corre en el deploy de producción.** Los deploys de otras ramas no lo ejecutan.
- Vercel llama a la ruta con `GET` y el encabezado `Authorization: Bearer <CRON_SECRET>`. Por eso `CRON_SECRET` tiene que ser igual a `ADMIN_API_TOKEN`.

Para comprobarlo, entrá a **Settings → Cron Jobs** del proyecto de la API. Desde ahí se ve la próxima ejecución, se puede lanzar a mano con **Run** y se puede consultar el log de cada corrida.

---

## Deploys siguientes

- Cada push a la rama de producción despliega automáticamente **los tres proyectos**, aunque el cambio sea en uno solo. Para evitarlo, en **Settings → Git → Ignored Build Step** de cada proyecto se puede poner este comando, que saltea el build si no hubo cambios dentro de su Root Directory:

  ```bash
  git diff HEAD^ HEAD --quiet -- .
  ```

- Cambiar una variable de entorno no redespliega nada por sí solo: hay que hacer *Redeploy*.
- Si un deploy de producción sale mal, en **Deployments** se puede elegir un deploy anterior y usar **Instant Rollback**.

### Previews y protección

Cada push a otra rama (o cada pull request) genera un deploy de *Preview* con una URL distinta, del tipo `https://appminds-widget-git-<rama>-<equipo>.vercel.app`. Hay que tener en cuenta que:

- Por defecto, Vercel protege los previews con **Vercel Authentication**: solo los ve quien inició sesión en Vercel y es miembro del equipo. Un iframe con un preview no carga en moodle-sim. Para probar el embebido, usá los dominios de producción.
- La API rechaza por CORS los orígenes que no estén en `CORS_ORIGENES`, así que un widget de preview no puede llamar a la API de producción salvo que agregues su URL.
- Las variables marcadas solo para *Production* no existen en los previews: una API de preview sin claves arranca, pero no puede responder consultas reales.

---

## Solución de problemas

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| El build de la API falla por tamaño | `sentence-transformers` o `langchain-huggingface` en `requirements.txt` | Sacarlas (paso 0). |
| `ModuleNotFoundError: No module named 'fastapi'` (u otro paquete) | La dependencia no está en la tabla `[project]` de `backend/pyproject.toml`. Vercel ignora `requirements.txt` si existe ese archivo | Agregarla en `dependencies` de `pyproject.toml` y volver a desplegar. |
| Todas las rutas de la API dan 500 | Una variable de entorno inválida | Ver el log de la función en **Logs** y corregir la variable que indica. |
| `/health` dice `"entorno": "local"` | Falta `ENTORNO=produccion` | Cargarla y hacer *Redeploy*. |
| El widget responde, pero con respuestas simuladas | Se compiló con `VITE_API_URL` vacía | Cargar la variable y hacer *Redeploy* del widget. |
| Error de CORS en la consola | `CORS_ORIGENES` no coincide exactamente con la URL del widget | Revisar `https://`, que no tenga barra final y que sea la URL de producción. Después, *Redeploy* de la API. |
| "Refused to display … in a frame" | La página que embebe no está en `frame-ancestors` | Editar `frontend/widget/vercel.json`, commitear y desplegar. |
| Se ve el lanzador, pero el chat no se abre | El origen de la página no está en `VITE_ORIGENES_PADRE` | Agregarlo y hacer *Redeploy* del widget. |
| `/admin/sync` o el cron dan 401 | Token distinto o de menos de 16 caracteres, o `CRON_SECRET` distinto de `ADMIN_API_TOKEN` | Igualar los dos valores y hacer *Redeploy* de la API. |
| `/admin/sync` da 503 | Sin acceso a Drive o a la base | Revisar que la carpeta esté compartida con la cuenta de servicio, que `GOOGLE_SERVICE_ACCOUNT_JSON` tenga el JSON completo y que el proyecto de Supabase no esté pausado. |
| La consulta da 429 | Se superó el límite de consultas de la sesión | Esperar lo que indica `Retry-After` o ajustar `LIMITE_SOLICITUDES_*`. |
| Errores de base después de días sin uso | Supabase pausó el proyecto por inactividad | Reanudarlo desde el panel de Supabase. |

## Limitaciones conocidas del despliegue

- **El límite de consultas por sesión se cuenta en memoria.** Cada instancia serverless de Vercel tiene su propio contador y lo pierde al reciclarse. Con varias instancias activas, el límite real puede ser más alto que el configurado. El detalle está en `backend/app/services/limite_solicitudes.py`.
- **El cron de Hobby es diario y con una hora aproximada.** Si un documento cambia en Drive, el asistente puede tardar hasta un día en reflejarlo, salvo que se lance `/admin/sync` a mano.
- **El plan Hobby es para uso no comercial.** Alcanza para el proyecto integrador. Si el instituto adopta el asistente, conviene revisar con quien administre la infraestructura dónde se aloja.
