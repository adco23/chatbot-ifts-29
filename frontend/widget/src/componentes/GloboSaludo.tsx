import { IconoCerrar } from './iconos';
import estilos from './GloboSaludo.module.css';

interface Props {
  alAbrir: () => void;
  alDescartar: () => void;
}

export function GloboSaludo({ alAbrir, alDescartar }: Props) {
  return (
    <div className={estilos.globo}>
      <button type="button" className={estilos.texto} onClick={alAbrir}>
        ¡Hola! ¿Te ayudo con tus primeros pasos?
      </button>
      <button type="button" className={estilos.descartar} onClick={alDescartar}>
        <IconoCerrar tamano={14} />
        <span className="solo-lectores">Cerrar el saludo</span>
      </button>
    </div>
  );
}
