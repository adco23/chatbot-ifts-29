import type { Tema } from '../config/temas';
import { clases } from '../util/clases';
import estilos from './BarraTemas.module.css';

interface Props {
  temas: readonly Tema[];
  /** Después de la primera consulta la barra pasa a una sola fila con desplazamiento horizontal. */
  compacta: boolean;
  deshabilitada: boolean;
  alElegir: (tema: Tema) => void;
}

export function BarraTemas({ temas, compacta, deshabilitada, alElegir }: Props) {
  if (temas.length === 0) return null;
  return (
    <div
      className={clases(estilos.barra, compacta && estilos.compacta)}
      role="group"
      aria-label="Temas de ayuda"
    >
      {temas.map((tema) => (
        <button
          key={tema.slug}
          type="button"
          className={estilos.chip}
          disabled={deshabilitada}
          onClick={() => alElegir(tema)}
        >
          {tema.texto}
        </button>
      ))}
    </div>
  );
}
