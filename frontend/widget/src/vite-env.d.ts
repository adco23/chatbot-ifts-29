/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_ORIGENES_PADRE?: string;
  readonly VITE_PREGUNTAS_SUGERIDAS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
