import type { FuenteChat } from '../api';
import { IconoPdf } from './iconos';
import estilos from './TarjetaFuente.module.css';

/** RF02: documento fuente de la respuesta, con enlace de descarga si el backend lo provee. */
export function TarjetaFuente({ fuente }: { fuente: FuenteChat }) {
  return (
    <div className={estilos.tarjeta}>
      <IconoPdf />
      <div className={estilos.datos}>
        <p className={estilos.titulo}>{fuente.titulo}</p>
        {fuente.enlace ? (
          <a
            className={estilos.enlace}
            href={fuente.enlace}
            target="_blank"
            rel="noopener noreferrer"
          >
            Descargar
            <span className="solo-lectores"> {fuente.titulo} (se abre en una pestaña nueva)</span>
          </a>
        ) : (
          <p className={estilos.sinEnlace}>Enlace no disponible</p>
        )}
      </div>
    </div>
  );
}
