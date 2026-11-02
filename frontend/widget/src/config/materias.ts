export interface Materia {
  id: number;
  nombre: string;
}

/**
 * Ids de la tabla `materia`, según el orden del INSERT en
 * backend/migrations/0001_esquema_inicial.sql. PENDIENTE: confirmar con el backend
 * (o exponer un endpoint de materias) porque dependen del orden de carga.
 */
export const MATERIAS: readonly Materia[] = [
  { id: 1, nombre: 'Técnicas de Programación' },
  { id: 2, nombre: 'Administración de Base de Datos' },
  { id: 3, nombre: 'Elementos de Análisis Matemático' },
  { id: 4, nombre: 'Lógica Computacional' },
];
