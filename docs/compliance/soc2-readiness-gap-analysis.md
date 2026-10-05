# StockAI: análisis de brechas frente a SOC 2 (Trust Services Criteria)

*Esto NO es un informe SOC 2 ni una certificación. StockAI no tiene hoy ninguna certificación
(ISO 27001, SOC 2); el propio DPA publicado lo dice. Este documento mapea los cinco criterios a los
controles que el código demuestra y enumera lo que falta para que un auditor externo pueda emitir un
informe Tipo I (diseño de controles en una fecha) y luego Tipo II (operación durante 3 a 12 meses).*

Escala de esfuerzo: **S** menos de 1 día · **M** unos días · **L** semanas · **XL** meses o depende
de un tercero.

Honestidad de alcance: SOC 2 evalúa sobre todo **procesos de la organización** (políticas escritas,
evaluación de riesgos, revisión de accesos, capacitación, gestión de proveedores, control de
cambios). El repositorio solo puede demostrar controles técnicos. Para todo lo organizativo, "sin
evidencia en el repositorio" significa **BRECHA o NO VERIFICADO** y requiere que el propietario
confirme si existe fuera del código.

---

## Seguridad (Common Criteria, obligatorio en todo informe SOC 2)

| Criterio | Controles implementados (evidencia) | Brechas | Esfuerzo |
|---|---|---|---|
| CC1 Entorno de control y gobierno | Reglas del proyecto escritas (`CLAUDE.md`), estándares de prueba obligatorios, backlog de estabilidad (`docs/stability.md`) | Sin código de conducta, organigrama, roles de seguridad designados, verificación de antecedentes ni capacitación en seguridad | M (documentos); el resto es organizativo |
| CC2 Información y comunicación | Páginas públicas de seguridad, privacidad, DPA y divulgación responsable (`Frontend/public/.well-known/security.txt`) | Plazos de notificación de brechas y de aviso de subencargados siguen como marcador `[confirmar]` en los textos publicados | S |
| CC3 Evaluación de riesgos | Este pack lista brechas técnicas conocidas | Sin registro de riesgos formal ni revisión periódica | M |
| CC4 Monitoreo de controles | `/health`, panel "Estado de la instalación" (`backend/service_config/ops.py:226 · def snapshot`), correo diario de fallos al operador, simulacro de restauración (`scripts/restore_drill.py`) | Sin monitoreo externo de disponibilidad ni alertas en tiempo real; el simulacro es manual | M |
| CC5 Actividades de control | Umbrales declarados en el registro (`backend/service_config/registry.py:821 · OPERATIONS = Service(`); suite de tests (`scripts/run_tests.py`) | **Sin CI/CD** (fuera de alcance por instrucción del propietario): los tests no son una compuerta automática antes de desplegar | Decisión del propietario |
| CC6.1 Acceso lógico | Roles `admin/analyst/viewer` (`backend/auth/guards.py:255 · def require_analyst_or_above`); operador de instalación separado (`backend/service_config/access.py:137 · def require_instance_operator`); API keys con alcance y vencimiento | **Sin MFA ni SSO**; sin RLS en Postgres | L (MFA), XL (SSO/SAML) |
| CC6.2 Alta/baja de usuarios | Invitaciones; suspender borra refresh tokens (`backend/users/service.py:275 · def update_status`) | Sin revisión periódica de accesos documentada; token ya emitido vale hasta 15 min | S-M |
| CC6.3 Mínimo privilegio | RBAC por rol; llaves `read`/`write`; el MCP solo lee (`backend/tests/test_mcp_server.py:150 · def test_every_tool_actually_only_calls_GET_endpoints`) | Acceso del operador a todos los tenants sin registro de accesos | M |
| CC6.1/6.7 Cifrado y protección de datos | bcrypt, hashes SHA-256 de tokens y llaves, Fernet para credenciales; TLS en el borde (Caddy) | HSTS ausente; datos de negocio y backups sin cifrado a nivel app; tráfico interno en claro | S (HSTS), M (backups), L (datos) |
| CC6.6 Perímetro | Solo Caddy expone 80/443 (`deploy/docker-compose.prod.yml:25 · "443:443"`); contenedores no root; CORS acotado; OpenAPI apagada en producción | Sin WAF ni límite por IP; firewall del proveedor NO VERIFICADO | M |
| CC6.8 Software malicioso / cadena de suministro | `THIRD_PARTY_NOTICES.md` (licencias) | Sin escaneo de dependencias (CVE), sin SBOM, sin firma de imágenes | M-L |
| CC7.1 Detección de vulnerabilidades | Política de divulgación responsable y `security.txt` | Sin pruebas de penetración ni escaneo periódico | XL (externo) |
| CC7.2 Detección de anomalías | Registro de acceso; límites de intentos que dejan huella | Sin SIEM, sin alertas por patrones de abuso | L |
| CC7.3-7.5 Respuesta a incidentes | `incident-response.md` (este pack) usando herramientas reales del producto | Sin ensayo, sin guardia 24x7, plazo de notificación a clientes sin definir | M |
| CC8.1 Gestión de cambios | Git, migraciones aditivas con rollback barato (`deploy/UPGRADE.md`), healthchecks en `deploy.sh` | Sin revisión de código obligatoria ni aprobación registrada; sin CI | M |
| CC9 Mitigación de riesgos / proveedores | `docs/data-that-leaves.md` lista cada salida | Sin evaluación formal de proveedores ni contratos verificables (marcador en el DPA); Voyage AI y Pinecone no declarados (ver `data-processing-and-subprocessors.md`) | M |

## Disponibilidad (A1)

| Criterio | Controles | Brechas | Esfuerzo |
|---|---|---|---|
| A1.1 Capacidad | Límites de recursos por contenedor (worker `cpus: 3.0`); techos por plan; panel con cola, pool de conexiones y disco con umbrales | Un solo VPS: sin balanceo ni réplica | XL |
| A1.2 Recuperación y respaldo | Backup nocturno de BD y `storage/`, retención 14 días (`deploy/README.md`); script con marcador de éxito (`deploy/ops/backup.sh`); recuperación de jobs huérfanos (`backend/workers/worker.py:41 · def recover_orphaned_jobs`); loops con ventana de recuperación (`backend/workers/loop_state.py:53 · DAILY_CATCHUP`) | **Backups en el mismo host (sin copia externa documentada)**; sin cifrado; RTO/RPO sin medir en producción | M |
| A1.3 Pruebas de recuperación | `scripts/restore_drill.py` mide tiempos y verifica integridad | Nadie lo ha corrido contra el backup de producción (pendiente del propietario); sin calendario | S |
| SLA con clientes | Texto de disponibilidad en los términos | Sin SLA medible respaldado por monitoreo externo | M |

## Integridad del procesamiento (PI1)

| Criterio | Controles | Brechas | Esfuerzo |
|---|---|---|---|
| PI1.1-1.3 Entradas completas y exactas | Validación de subida por extensión y tamaño; chequeos de calidad de datos y diagnóstico antes de entrenar; máquina de estados de sesiones (`backend/sessions/state_machine.py`) | Sin firma de integridad de los archivos subidos | S |
| PI1.4 Salidas | Manifiesto de linaje inmutable por corrida (tabla `session_manifests` con trigger, hash del dataset y del forecast, versiones) (`backend/lineage/manifest.py`) | El hash de dataset no se verifica de forma automática en operación (solo en el simulacro) | S |
| PI1.5 Almacenamiento | Persistencia transaccional; borrado de tenant atómico; test de que el borrado cubre todas las tablas | — | — |
| Fallos visibles | Política de no fallar en silencio (skill `silent-failures`); resumen diario de fallos | — | — |

## Confidencialidad (C1)

| Criterio | Controles | Brechas | Esfuerzo |
|---|---|---|---|
| C1.1 Identificar y proteger información confidencial | Aislamiento por tenant con tests cruzados (`backend/tests/test_permission_audit.py:2 · Permission-pair + cross-tenant audit`); secretos nunca devueltos por la API | Sin clasificación formal de datos; sin RLS | M-L |
| C1.2 Eliminación | `DELETE /tenant` (`backend/tenants/data_export.py:233 · def delete_tenant`); cuentas de prueba borradas a las 24 h | No propaga a backups (14 días) ni a subencargados; sin certificado de borrado | M |

## Privacidad (P1 a P8)

| Criterio | Controles | Brechas | Esfuerzo |
|---|---|---|---|
| P1 Aviso | Política de privacidad y de cookies en la app | Marcadores `[confirmar]` (razón social, servidor, logs) aún sin completar | S |
| P2 Elección y consentimiento | Aceptación de términos con fecha y versión; IA e integraciones opcionales por instalación | IA no apagable por cuenta | M |
| P3-P4 Recolección y uso | Solo se usan datos del tenant para entrenar su modelo; sin analítica | — | — |
| P5 Retención y eliminación | Reglas en `data-processing-and-subprocessors.md` | Auditoría y logs sin retención definida; sesiones permanentes por diseño | M |
| P6 Acceso | Exportación completa del tenant en ZIP | Sin flujo de DSAR por individuo | M |
| P6.4-6.5 Divulgación a terceros | Lista de subencargados y de qué reciben | Discrepancia Voyage/Pinecone; sin registro de a quién se envió qué | S-M |
| P7 Calidad | Edición de datos en la app | — | — |
| P8 Monitoreo y cumplimiento | Registro de auditoría de acciones | Sin responsable de privacidad designado ni procedimiento de quejas | S |

---

## Ruta mínima hacia un informe Tipo I (orden sugerido)

1. **S:** HSTS; completar los marcadores `[confirmar]` de los textos legales; confirmar y declarar
   Voyage/Pinecone; correr el simulacro de restauración contra el backup real y archivar el
   resultado.
2. **S-M:** cifrar los backups y copiarlos fuera del VPS; registrar accesos del operador;
   rotar el refresh token.
3. **M:** políticas escritas (acceso, cambios, incidentes, proveedores, retención), registro de
   riesgos, revisión trimestral de accesos, escaneo de dependencias manual periódico.
4. **L:** MFA; migrar tokens a cookies `httpOnly` y quitar `unsafe-inline` de la CSP; auditoría
   inmutable con retención.
5. **XL (externo):** pentest independiente; elegir auditor; 3 a 12 meses de evidencia para Tipo II.

El orden de las filas 1 a 3 produce la mayor reducción de riesgo por unidad de esfuerzo y no
requiere reabrir producto.
