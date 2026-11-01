# Consultorio Dra. Laura — recordatorios por WhatsApp + Google Calendar

Software para la Dra Laura y su asistente. Resuelve dos problemas: **inasistencias** (recordatorios
automáticos por WhatsApp) y **trabajo manual** (cada cita se agenda sola en Google Calendar).
Sin facturación ni expedientes, y sin calendario propio: el calendario es Google Calendar.

## Qué hace

| Spec | Cómo se cumple |
|---|---|
| WhatsApp 7 días antes, con Confirmar / Cancelar | Plantilla con 2 botones de respuesta rápida (`recordatorio_cita_7d`) |
| Recordatorio 1 día antes, sin botones, no se envía si la cita está cancelada | `recordatorio_cita_1d`; el scheduler omite las citas canceladas |
| Confirmar/cancelar se notifica a Laura y su asistente | Aviso en el panel **y** WhatsApp a los números de `STAFF_PHONES` |
| Evento en Google Calendar "Cita de *paciente*" | Se crea al agendar, a la fecha y hora de la cita |
| Número inexistente / sin WhatsApp: avisa | Validación al anotar + detección al enviar (ver "Decisiones") |
| Sin respuesta en 5 días: avisa | Aviso único, contado desde el recordatorio de 7 días |

## Stack y decisiones

- **Python + FastAPI.** Un solo proceso sirve la API, el panel web y el webhook de WhatsApp.
- **SQLite** (un archivo, `sqlite3` de la librería estándar). Es un consultorio con dos usuarios: no hace falta un servidor de base de datos ni backups complejos (basta copiar el archivo).
- **WhatsApp Cloud API oficial de Meta**, no librerías no oficiales (tipo WhatsApp Web): estas pueden provocar el bloqueo del número del consultorio. Contrapartida: los mensajes que inicia el consultorio deben usar **plantillas aprobadas por Meta** (abajo están los textos).
- **Google Calendar con cuenta de servicio:** no hay pantallas de login ni tokens que se venzan; la Dra Laura solo comparte su calendario con el correo de la cuenta de servicio.
- **Scheduler propio** (una tarea asyncio que corre cada minuto) en vez de Celery/cron: el estado vive en la base de datos, así que si el servidor se reinicia no se pierden ni se duplican recordatorios. Cada recordatorio tiene su estado (`pending/sent/failed/skipped`).
- **Lógica separada del framework.** Todo el negocio está en `app/service.py` y `app/webhook.py`; `app/main.py` solo expone rutas. Por eso los criterios de aceptación se prueban sin levantar un servidor, con WhatsApp, Calendar y reloj simulados.
- **Interfaz sin build:** HTML + CSS + JS puro, siguiendo `SKILLS.md` (fondo marrón claro, menús negro/blanco, azul para lo principal, botones secundarios integrados al fondo, Gotham → Metropolis, Arial negrita para destacar, avisos de 5 s apilados con botón de cerrar y panel de notificaciones persistente). El bloque **"Requieren atención"** sube arriba de la lista para que nada importante quede escondido.
- **Acceso al panel con usuario y clave** (HTTP Basic, uno por persona en `PANEL_USERS`). Hay datos de pacientes, así que la app se niega a arrancar sin usuarios.

### Reglas que conviene conocer
- **Horario de envío:** los recordatorios solo salen entre 08:00 y 21:00 (hora local; configurable). Si toca de madrugada, sale en el primer minuto de la franja.
- **Cita agendada con poco margen:** si faltan menos de 7 días, el primer aviso sale enseguida; si faltan menos de 24 h, solo sale el recordatorio final.
- **Número sin WhatsApp:** se avisa una sola vez, se dejan de enviar mensajes a ese número y la cita aparece en "Requieren atención" para gestionarla a mano (hay botones para marcarla confirmada o cancelarla manualmente).
- **Errores temporales** (sin internet, caída de Meta): se reintenta cada minuto hasta 3 veces y después se avisa.
- **Respuestas:** funcionan los botones y también texto simple ("sí", "confirmo", "no", "cancelo"). Un mensaje repetido no genera avisos duplicados.

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # completa PANEL_USERS, STAFF_PHONES y el resto
set -a; source .env; set +a
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
```

Abre `http://localhost:8000`. Sin `WA_TOKEN` los WhatsApp se **simulan en el log** y sin
`GOOGLE_CALENDAR_ID` no se crean eventos: sirve para probar la interfaz antes de configurar nada.
Ejecuta **un solo proceso** (el scheduler vive dentro de la app) y publícala detrás de HTTPS (por
ejemplo con Caddy o nginx): hace falta para el webhook de Meta y para proteger las claves del panel.

### WhatsApp (Meta Cloud API)
1. Crea una app en Meta for Developers, agrega WhatsApp y registra el número del consultorio.
2. Crea estas **plantillas** (categoría *Utility*, idioma `es`) con los mismos nombres:
   - `recordatorio_cita_7d` — «Hola {{1}}, te recordamos tu cita con la Dra Laura el {{2}} a las {{3}}. ¿Confirmas tu asistencia?» + 2 botones de respuesta rápida: **Confirmar** y **Cancelar**.
   - `recordatorio_cita_1d` — «Recuerda que mañana tienes tu cita con la Dra Laura, {{1}}.»
   - `aviso_consultorio` — «Aviso del consultorio: {{1}}»
3. Webhook: URL `https://TU-DOMINIO/webhook/whatsapp`, token = `WA_VERIFY_TOKEN`, suscripción al campo `messages`. Pon el *App Secret* en `WA_APP_SECRET` para que se valide la firma de cada llamada.
4. Completa `WA_TOKEN` y `WA_PHONE_NUMBER_ID`.

### Google Calendar
1. En Google Cloud crea un proyecto, habilita *Google Calendar API* y crea una **cuenta de servicio**; descarga su JSON (`GOOGLE_CREDENTIALS_FILE`).
2. En Google Calendar comparte el calendario de la Dra Laura con el correo de esa cuenta, con permiso **"Hacer cambios en eventos"**.
3. Copia el ID del calendario en `GOOGLE_CALENDAR_ID`.

### Tipografías
Gotham tiene licencia comercial; si no está instalada en el equipo se usa Metropolis y, si tampoco, una sans-serif del sistema. Para incluirlas, copia los `.woff2` a `app/static/fonts/` con los nombres que figuran en `style.css` (`Gotham-Book.woff2`, `Gotham-Medium.woff2`, `Metropolis-Regular.woff2`, `Metropolis-Medium.woff2`).

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

- `tests/test_acceptance.py` — los 5 criterios de aceptación (más casos de borde): recordatorios a 7 y 1 día, notificación a Laura y asistente, evento de Calendar, número inválido / sin WhatsApp, y falta de respuesta a los 5 días. Usa dobles de WhatsApp y Calendar y un reloj simulado.
- `tests/test_api.py` — capa HTTP: login, validaciones, webhook y firma. Se omite si FastAPI/httpx no están instalados.

## Supuestos y puntos a confirmar con la Dra Laura
- **No se puede saber de antemano si un número tiene WhatsApp:** la API oficial no ofrece esa consulta. Se detecta al enviar (error inmediato o aviso posterior de Meta, código 131026). Por eso, si la cita es en más de 7 días, el aviso de "número sin WhatsApp" llega cuando sale el primer recordatorio. Al anotar sí se rechazan números mal escritos. Si prefieren saberlo al agendar, se puede enviar un mensaje de confirmación inmediato (requiere otra plantilla).
- **Cancelación y calendario:** si el paciente cancela, el evento de Google Calendar **no se borra** (el spec no lo pide). Es fácil agregarlo si lo quieren.
- **Texto del recordatorio de 1 día:** el spec dice «…con la Dra Laura *nombre del paciente*»; se interpretó como «Recuerda que mañana tienes tu cita con la Dra Laura, {nombre}.»
- **Sin respuesta:** se mide desde el recordatorio de 7 días; si la cita se agenda con menos de 5 días de margen no se genera ese aviso.
- **Números argentinos:** conviene cargarlos con código de país (`+54 9 11 …`). Sin código se asume `+54`.
