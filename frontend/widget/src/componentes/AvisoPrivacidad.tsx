import estilos from './AvisoPrivacidad.module.css';

// PARA REVISIÓN: pantalla sin diseño (RF06).
export function AvisoPrivacidad({ alAceptar }: { alAceptar: () => void }) {
  return (
    <div className={estilos.aviso} role="note" aria-label="Aviso de privacidad" data-para-revision>
      <p className={estilos.texto}>
        <strong>Tu privacidad:</strong> este asistente no te pide ni guarda datos personales. No
        escribas tu nombre, DNI, correo ni otros datos tuyos en las consultas.
      </p>
      <button type="button" className={estilos.aceptar} onClick={alAceptar}>
        Entendido
      </button>
    </div>
  );
}
