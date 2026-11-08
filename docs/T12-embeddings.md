# T12: Decisión Técnica sobre el Modelo y Dimensión de Embeddings (T12)

- **Estado:** Aprobado / Implementado
- **Fecha:** Octubre 2026
- **Contexto:** 
  Para el funcionamiento del núcleo RAG del asistente virtual del IFTS N.° 29, es necesario almacenar y comparar vectores de embeddings en Supabase utilizando la extensión `pgvector`. 

- **Decisión:**
  1. Se utilizará el modelo de embeddings provisto por **Google Gemini**, dado que es el motor principal del LLM para el proyecto.
  2. Se define una dimensión fija de **768** para la columna vector en la base de datos (`vector(768)`), acorde al estándar de salida de los embeddings de Gemini.
  3. **Respaldo:** Se establece que los vectores de Gemini y de Cloudflare (proveedor secundario de respaldo) no son intercambiables directamente en un mismo índice debido a diferencias en dimensiones y espacios vectoriales. En caso de requerirse un failover masivo al proveedor de respaldo, se prevé una rutina de reindexación previa de la base documental.

- **Consecuencias:**
  - Permite consultas semánticas rápidas y precisas dentro de las tablas de fragmentos.
  - Asegura la compatibilidad directa con las respuestas generadas por los modelos de Google.