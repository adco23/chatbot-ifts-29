/**
 * Tipos del contrato de `POST /chat`, copiados de backend/app/models/chat.py.
 * Si el backend cambia, actualizar acá y en docs/api-contract.md.
 */

export interface ContextoChat {
  materia_id?: number | null;
  comision?: string | null;
  periodo?: string | null;
  consulta_pendiente?: string | null;
}

export interface SolicitudChat {
  /** Entre 1 y 2000 caracteres. */
  mensaje: string;
  contexto: ContextoChat;
}

export interface FuenteChat {
  documento_id: number;
  drive_id: string;
  titulo: string;
  enlace: string | null;
  fragmento_ids: number[];
}

export type EstadoRespuesta = 'respondida' | 'requiere_contexto' | 'sin_evidencia' | 'bloqueada';

export type CampoRequerido = 'materia_id' | 'comision';

export interface RespuestaChat {
  estado: EstadoRespuesta;
  respuesta: string;
  fuentes: FuenteChat[];
  confianza: number;
  umbral: number;
  /** RF07: recordatorio de verificación en consultas sobre fechas, inscripciones o exámenes. */
  advertencia_temporal: string | null;
  /** Contexto resuelto por el backend. El widget lo reenvía tal cual en la consulta siguiente. */
  contexto: ContextoChat;
  campo_requerido: CampoRequerido | null;
}

export const LARGO_MAXIMO_MENSAJE = 2000;

/** Categorías de error que la interfaz sabe mostrar. */
export type TipoErrorApi =
  | 'sesion_invalida' // 400
  | 'validacion' // 422
  | 'limite' // 429
  | 'servicio_no_disponible' // 503
  | 'red' // sin conexión o CORS
  | 'tiempo_agotado'
  | 'desconocido';

export class ErrorApi extends Error {
  readonly tipo: TipoErrorApi;
  /** Segundos sugeridos por `Retry-After` (solo en 429). */
  readonly reintentarEn: number | undefined;

  constructor(tipo: TipoErrorApi, mensaje: string, reintentarEn?: number) {
    super(mensaje);
    this.name = 'ErrorApi';
    this.tipo = tipo;
    this.reintentarEn = reintentarEn;
  }
}
