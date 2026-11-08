// Íconos propios en SVG (sin librerías ni servicios externos). Decorativos: aria-hidden.

interface PropsIcono {
  tamano?: number;
}

export function IconoRobot({ tamano = 32 }: PropsIcono) {
  return (
    <svg
      width={tamano}
      height={tamano}
      viewBox="0 0 32 32"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <line x1="16" y1="4.5" x2="16" y2="9" />
      <circle cx="16" cy="4" r="1.6" fill="currentColor" stroke="none" />
      <rect x="6" y="9" width="20" height="16" rx="5" />
      <circle cx="12" cy="17" r="1.9" fill="currentColor" stroke="none" />
      <circle cx="20" cy="17" r="1.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconoCerrar({ tamano = 20 }: PropsIcono) {
  return (
    <svg
      width={tamano}
      height={tamano}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <line x1="4" y1="4" x2="16" y2="16" />
      <line x1="16" y1="4" x2="4" y2="16" />
    </svg>
  );
}

export function IconoPdf() {
  return (
    <svg width="44" height="54" viewBox="0 0 44 54" aria-hidden="true" focusable="false">
      <path
        d="M4 0h26l14 14v36a4 4 0 0 1-4 4H4a4 4 0 0 1-4-4V4a4 4 0 0 1 4-4z"
        fill="var(--color-naranja)"
      />
      <path d="M30 0v10a4 4 0 0 0 4 4h10z" fill="#ffffff" opacity="0.35" />
      <text
        x="22"
        y="44"
        textAnchor="middle"
        fill="#ffffff"
        fontFamily="inherit"
        fontSize="13"
        fontWeight="700"
      >
        PDF
      </text>
    </svg>
  );
}
