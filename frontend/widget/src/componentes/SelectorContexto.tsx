import type { CampoRequerido, ContextoChat } from '../api';
import { COMISIONES_EJEMPLO } from '../config/comisiones';
import { MATERIAS } from '../config/materias';
import estilos from './SelectorContexto.module.css';

interface Props {
  campo: CampoRequerido;
  deshabilitado: boolean;
  alElegir: (texto: string, cambios: Partial<ContextoChat>) => void;
}

// PARA REVISIÓN: pantalla sin diseño (RF03). Las comisiones son de ejemplo hasta que las
// provea el backend.
export function SelectorContexto({ campo, deshabilitado, alElegir }: Props) {
  const opciones =
    campo === 'materia_id'
      ? MATERIAS.map((m) => ({
          clave: String(m.id),
          texto: m.nombre,
          cambios: { materia_id: m.id },
        }))
      : COMISIONES_EJEMPLO.map((c) => ({
          clave: c,
          texto: `Comisión ${c}`,
          cambios: { comision: c },
        }));

  return (
    <div className={estilos.selector} data-para-revision>
      <div
        className={estilos.opciones}
        role="group"
        aria-label={campo === 'materia_id' ? 'Elegí la materia' : 'Elegí la comisión'}
      >
        {opciones.map((opcion) => (
          <button
            key={opcion.clave}
            type="button"
            className={estilos.opcion}
            disabled={deshabilitado}
            onClick={() => alElegir(opcion.texto, opcion.cambios)}
          >
            {opcion.texto}
          </button>
        ))}
      </div>
      <p className={estilos.ayuda}>
        {campo === 'comision'
          ? 'Si tu comisión no está en la lista, escribila abajo.'
          : 'También podés escribirla abajo.'}
      </p>
    </div>
  );
}
