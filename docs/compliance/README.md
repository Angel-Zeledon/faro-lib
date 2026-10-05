# Paquete de cumplimiento de StockAI

Documentos para entregar a equipos de seguridad de clientes. Escritos en español para el
propietario; los identificadores del código están en inglés. **Cada afirmación sobre un control
está verificada contra el repositorio; lo que no está implementado se llama BRECHA.**

| Documento | Para qué |
|---|---|
| `security-overview.md` | Arquitectura, cifrado, secretos, autenticación, RBAC, auditoría, límites, API keys, aislamiento |
| `data-processing-and-subprocessors.md` | Datos personales, retención, exportación/borrado, subencargados reales |
| `soc2-readiness-gap-analysis.md` | Los cinco Trust Services Criteria frente al código, con brechas y esfuerzo |
| `incident-response.md` | Qué hacer ante un incidente con las herramientas que existen |
| `business-continuity.md` | Backups, RPO/RTO medibles con el simulacro, escenarios |
| `vulnerability-disclosure.md` | Enlace a `/divulgacion-responsable` y pendientes |
| `questionnaire-answers.md` | Respuestas Sí/No/Parcial a las preguntas habituales, con evidencia |

## Convención de citas (la comprueba un test)

Una cita es un fragmento de código en backticks con la forma

```
`ruta/al/archivo.py:123 · símbolo`
```

El símbolo debe aparecer literalmente en esa línea. Si el código se mueve, el test
`backend/tests/test_compliance_citations_are_real.py` falla y hay que **re-apuntar la cita**, no
borrar el símbolo: un documento de cumplimiento con una cita podrida afirma un control que quizá ya
no existe. Las rutas de archivo sin línea también se comprueban (que existan).

## Estados

**Implementado** · **Parcial** · **BRECHA** (no existe) · **NO VERIFICADO** (depende de algo fuera
del repositorio, por ejemplo el servidor de Hostinger; hay que confirmarlo antes de contestar "Sí").

## Qué debe confirmar el propietario antes de usar este pack

1. Si `services.rag` está activo en producción (`/health`): Voyage AI y Pinecone no están en la
   lista pública de subencargados.
2. Los marcadores `[confirmar]` de `Frontend/src/i18n/legal.ts` y `legalExtra.ts` (ubicación del
   servidor, plazo de notificación de brechas, plazo de logs, contratos con subencargados).
3. El contenido real y el horario de `/opt/stockai-ops/backup.sh`, y correr
   `scripts/restore_drill.py` (ver `deploy/RESTORE.md`) para tener un RTO medido.
4. Cifrado de disco y firewall del VPS en Hostinger.
