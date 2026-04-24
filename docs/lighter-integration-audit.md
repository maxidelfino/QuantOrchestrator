# Lighter en QuantOrchestrator: `lighter-agent-kit` y `lighter-python`

## 1. Resumen ejecutivo

Este documento resume qué aprendimos de `lighter-agent-kit` y `lighter-python` desde la perspectiva de diseño de bots para QuantOrchestrator.

Conclusión corta:

- `lighter-python` es el componente importante. Es un SDK Python público para interactuar con Lighter, con APIs REST, firma de transacciones, websocket y utilidades de paper trading.
- `lighter-agent-kit` no reemplaza un motor de ejecución serio. Es más bien un wrapper/skill para agentes y uso asistido por lenguaje natural, construido encima de `lighter-python`.
- Para este proyecto, el valor real está en usar `lighter-python` como base de integración técnica, no en delegar la ejecución crítica a `lighter-agent-kit`.
- Ninguno de los dos, por sí solos, equivale a un OMS/EMS production-grade para operar con fondos reales.
- La instalación recomendada por upstream para `lighter-agent-kit` usa `curl | bash`. En este repo no deberíamos adoptar ese patrón ciegamente. Conviene clonar, revisar, fijar versión, instalar en entorno aislado y validar manualmente.

Criterio práctico para QuantOrchestrator:

- `lighter-python`: sí, potencialmente útil como SDK base.
- `lighter-agent-kit`: útil para exploración, debugging, paper trading y workflows manuales/asistidos.
- Para real money: hace falta una capa propia encima con control de riesgo, idempotencia, observabilidad, reconciliación y kill switches.

---

## 2. Qué es `lighter-agent-kit`

`lighter-agent-kit` es un kit orientado a agentes de IA para operar o consultar Lighter usando lenguaje natural.

En términos concretos, expone scripts Python que un agente puede invocar para:

- consultar mercado y cuenta,
- enviar órdenes,
- cancelar/modificar órdenes,
- mover fondos,
- correr paper trading local,
- responder en JSON a un agente que orquesta esas llamadas.

Según su README público, está pensado para integrarse con agentes que soportan la especificación de `agentskills.io`, por ejemplo Claude Code, Cursor, Codex, Devin u otros similares.

Piezas visibles del repo:

- `scripts/query.py`: lecturas de mercado y cuenta.
- `scripts/trade.py`: escrituras firmadas sobre Lighter.
- `scripts/paper.py`: simulación local.
- `SKILL.md`: contrato de integración para agentes.
- `install.sh`: instalador interactivo.
- `requirements.lock`: dependencias fijadas.

Lectura honesta:

- No parece un framework completo de trading algorítmico.
- Parece una capa de acceso “agent-friendly” por encima del SDK.
- Es útil para workflows humanos-asistidos, demos, prototipos y automatización guiada.
- No hay evidencia pública suficiente para tratarlo como runtime robusto para producción.

---

## 3. Qué es `lighter-python`

`lighter-python` es el SDK Python público para Lighter.

Según la documentación pública y la estructura del repositorio, ofrece:

- cliente REST para endpoints de Lighter,
- `SignerClient` para transacciones firmadas,
- `WsClient` para websocket,
- tipos/modelos generados desde OpenAPI,
- ejemplos de lectura de mercado,
- ejemplos de creación/cancelación de órdenes,
- utilidades de paper trading.

Capacidades observables públicamente:

- lectura de account info, order books, trades, candles, funding, etc.,
- envío de transacciones a `/api/v1/sendTx` y `/api/v1/sendTxBatch`,
- obtención de `nextNonce`,
- websocket para sincronizar order books/accounts,
- paper trading local con ejemplos `snapshot`, `live` y `health`.

Punto importante:

`lighter-python` no es solo un cliente OpenAPI generado. También exporta componentes operativos como:

- `WsClient`,
- `SignerClient`,
- `create_api_key`,
- clases de `paper_client`.

Eso lo vuelve bastante más relevante que un simple wrapper REST.

---

## 4. Cómo se relacionan entre sí

La relación es simple:

- `lighter-python` es la base técnica.
- `lighter-agent-kit` se apoya sobre esa base para darle una interfaz de uso por scripts + agentes.

Dicho más claro:

- si queremos construir integración seria con Lighter, el SDK a entender es `lighter-python`;
- si queremos una capa rápida para pruebas operativas, consultas o ejecución asistida por un agente, `lighter-agent-kit` puede acelerar.

Evidencia pública relevante:

- `lighter-agent-kit` fija `lighter-python` en `requirements.lock` con una dependencia Git a un commit específico:
  - `lighter-sdk @ git+https://github.com/elliottech/lighter-python.git@4fedf1648ea032dd9437d6c6e82b0533eadc3c4c`

Eso confirma que el agent kit depende del SDK y no al revés.

---

## 5. Para qué nos sirven realmente en el diseño de bots

### Lo que sí nos aportan

#### `lighter-python`

Sirve como base para:

- construir conectores a Lighter,
- consultar metadata de mercado,
- consumir order books y trades,
- firmar y enviar órdenes,
- hacer prototipos de ejecución,
- validar flujos de cuenta,
- armar herramientas internas de observabilidad y reconciliación,
- simular ciertos escenarios con paper trading.

En QuantOrchestrator, eso encaja bien en estas capas:

- `market data adapter`,
- `execution adapter`,
- `account adapter`,
- `paper/sim harness`,
- utilidades de investigación rápida.

#### `lighter-agent-kit`

Sirve como acelerador para:

- exploración manual,
- pruebas rápidas desde prompts,
- debugging de un flujo de órdenes,
- paper trading asistido,
- validación operativa inicial de credenciales/comandos,
- demos internas de cómo interactuar con Lighter.

### Lo que no deberíamos pedirles

No deberíamos asumir que resuelven por nosotros:

- gestión robusta de riesgo,
- idempotencia de órdenes,
- deduplicación ante reintentos,
- reconciliación post-trade,
- manejo serio de reconexión websocket,
- persistencia transaccional,
- auditoría,
- kill switches,
- circuit breakers,
- control de exposición multi-estrategia,
- separación segura entre research, paper y live.

### Juicio práctico para este repo

Recomendación:

- usar `lighter-python` como ladrillo técnico;
- usar `lighter-agent-kit` solo como herramienta auxiliar;
- no diseñar la arquitectura de bots alrededor del agent kit.

---

## 6. Limitaciones y riesgos de seguridad/supply chain

## Limitaciones técnicas observables

### `lighter-agent-kit`

- Tiene muy pocos commits públicos en comparación con un framework maduro.
- Está orientado a scripts y skill integration, no a un motor de ejecución institucional.
- El paper trading es local y con alcance limitado.
- Según la documentación pública, el paper trading está restringido en varios aspectos y no replica todo el comportamiento real.

### `lighter-python`

- Hay señales de madurez parcial, pero no de “stack de ejecución completo”.
- No vimos evidencia pública clara de:
  - ledger persistente de órdenes/intentos,
  - retries seguros para writes,
  - reconciliación automática fuerte,
  - validación robusta de secuencia/replay en websocket,
  - capa integrada de circuit breaker.

### Gaps operativos

Para fondos reales faltaría, como mínimo:

- almacenamiento duradero de intents y fills,
- order state machine propia,
- reintentos controlados con semántica clara,
- control de nonces y duplicados,
- verificación post-envío,
- reconciliación periódica entre exchange state y estado interno,
- alertas y kill switches.

## Riesgos de supply chain

### Riesgo 1: instalación con `curl | bash`

Verificado en el README público de `lighter-agent-kit`:

```bash
curl -fsSL https://github.com/elliottech/lighter-agent-kit/releases/latest/download/install.sh | bash
```

Esto no es aceptable como práctica por defecto en un proyecto con ambición de operar bots.

Problemas:

- ejecuta shell remoto sin revisión previa,
- el contenido puede cambiar entre ejecuciones,
- complica auditoría,
- abre superficie de riesgo innecesaria.

### Riesgo 2: el instalador hace más de lo que parece

Verificado revisando `install.sh`:

- descarga y ejecuta lógica shell interactiva,
- puede intentar instalar Python o Git,
- puede invocar comandos con privilegios elevados,
- descarga binarios auxiliares como `gum`,
- escribe credenciales locales.

Eso no significa que sea malicioso. Significa que operativamente hay que tratarlo como software a auditar, no como una línea “mágica”.

### Riesgo 3: dependencia Git pinneada

Verificado en `requirements.lock` de `lighter-agent-kit`:

```text
lighter-sdk @ git+https://github.com/elliottech/lighter-python.git@4fedf1648ea032dd9437d6c6e82b0533eadc3c4c
```

Esto tiene un lado bueno y uno malo.

Bueno:

- evita depender del `main` flotante.

Malo:

- depende de GitHub como origen,
- no necesariamente de un artefacto firmado/reproducible,
- requiere validar ese commit antes de confiar.

### Riesgo 4: inconsistencias de versionado visibles

Verificado públicamente:

- el repo/release de `lighter-python` muestra `v1.0.9`,
- pero `lighter/__init__.py` expone `__version__ = "1.0.0"`.

Eso no prueba un fallo crítico, pero sí una inconsistencia que obliga a verificar qué versión real estamos consumiendo.

## Riesgos de seguridad operativa

- manejo de API private keys,
- posibilidad de órdenes irreversibles,
- retiros irreversibles,
- mezcla accidental entre paper y live,
- ejecución por prompts ambiguos si se usa con agentes,
- exposición excesiva si no hay límites externos al SDK.

---

## 7. Instalación paso a paso priorizando seguridad operacional

## Principios

Para este repo, la instalación recomendada debería seguir estas reglas:

- no usar `curl | bash`;
- usar entorno aislado;
- fijar versión o commit;
- revisar archivos críticos antes de instalar;
- probar primero solo lectura;
- separar credenciales de desarrollo, paper y live.

## Enfoque recomendado

### Paso 1. Crear un entorno aislado

Verificado como práctica recomendada general. No depende del upstream.

```bash
python -m venv .venv-lighter
source .venv-lighter/bin/activate
python -m pip install --upgrade pip
```

En Windows PowerShell:

```powershell
python -m venv .venv-lighter
.\.venv-lighter\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

### Paso 2. Clonar los repositorios, no ejecutar instaladores remotos

Verificado como alternativa más segura al README de `lighter-agent-kit`.

```bash
git clone https://github.com/elliottech/lighter-python.git vendor/lighter-python
git clone https://github.com/elliottech/lighter-agent-kit.git vendor/lighter-agent-kit
```

### Paso 3. Fijar explícitamente commit o tag

Esto requiere validación manual de qué versión exacta queremos aprobar.

Ejemplo:

```bash
git -C vendor/lighter-python checkout <tag-o-commit-aprobado>
git -C vendor/lighter-agent-kit checkout <tag-o-commit-aprobado>
```

Validación manual necesaria:

- revisar release notes,
- revisar diff contra la versión anterior,
- revisar issues abiertas relevantes,
- decidir si aprobamos tag o commit.

### Paso 4. Revisar archivos sensibles antes de instalar

Verificado como paso razonable; los nombres de archivo sí están verificados públicamente.

Revisar al menos:

- `vendor/lighter-agent-kit/install.sh`
- `vendor/lighter-agent-kit/requirements.lock`
- `vendor/lighter-agent-kit/scripts/`
- `vendor/lighter-python/pyproject.toml`
- `vendor/lighter-python/lighter/__init__.py`

Preguntas mínimas:

- ¿qué se descarga?
- ¿qué se instala?
- ¿qué pide privilegios?
- ¿dónde guarda credenciales?
- ¿qué versión/commit de `lighter-python` se consume?
- ¿hay writes habilitados por defecto?

### Paso 5. Instalar `lighter-python` de forma explícita

Esto puede hacerse de varias formas.

#### Opción A. Instalar desde el checkout local

Más auditable.

```bash
python -m pip install ./vendor/lighter-python
```

#### Opción B. Instalar desde commit Git explícito

Útil si no queremos mantener checkout local, pero menos auditable que instalar desde carpeta revisada.

```bash
python -m pip install "git+https://github.com/elliottech/lighter-python.git@<commit-aprobado>"
```

### Paso 6. Instalar dependencias del agent kit sin usar su instalador shell

Si realmente queremos evaluarlo, mejor hacerlo así:

```bash
python -m pip install -r vendor/lighter-agent-kit/requirements.lock
```

Importante:

- esto está verificado como archivo existente;
- la seguridad real depende de revisar el lock antes;
- si el lock referencia commits Git, hay que aprobarlos manualmente.

### Paso 7. Probar primero solo lectura

Verificado conceptualmente contra capacidades públicas del SDK.

Ejemplo simple con `lighter-python`:

```bash
python -c "import lighter; print(lighter)"
```

Ejemplo con scripts del agent kit en modo lectura:

```bash
python vendor/lighter-agent-kit/scripts/query.py --help
python vendor/lighter-agent-kit/scripts/query.py market list
```

La sintaxis exacta del segundo comando está apoyada por el README público, pero igual conviene validarla localmente porque los CLIs cambian.

### Paso 8. Configurar credenciales fuera del repo

No guardar claves en el repo. Nunca.

Preferir:

- variables de entorno cargadas desde un secret manager,
- o archivos fuera del repo con permisos estrictos.

Ejemplo:

```bash
export LIGHTER_API_PRIVATE_KEY="<valor>"
export LIGHTER_ACCOUNT_INDEX="<valor>"
export LIGHTER_API_KEY_INDEX="<valor>"
export LIGHTER_HOST="https://mainnet.zklighter.elliot.ai"
```

Esto está verificado como patrón soportado por `lighter-agent-kit` según su README.

### Paso 9. Validar en testnet o paper antes de live

Verificado en espíritu por la propia documentación del proyecto upstream, que advierte sobre irreversibilidad y recomienda paper trading primero.

### Paso 10. No habilitar writes desde agentes sin guardrails

Recomendación de seguridad para este repo:

- primero consultas,
- después paper,
- después live con confirmación manual,
- recién mucho más tarde live automatizado.

---

## 8. Flujo recomendado de uso futuro en este repo

## Patrón propuesto

### Etapa 1. Research y market inspection

Usar `lighter-python` o `lighter-agent-kit` solo para:

- listar mercados,
- leer funding,
- leer order books,
- inspeccionar metadata,
- relevar límites y fees.

Objetivo:

- entender microestructura,
- validar símbolos,
- conocer tamaños mínimos, tick size, routes y restricciones.

### Etapa 2. Paper harness

Usar el paper trading solo para:

- validar wiring técnico,
- probar parsing de señales,
- medir tiempos y flujo de órdenes,
- detectar fallos de integración.

No usarlo para inferir edge real sin más. Paper no reemplaza validación de ejecución real.

### Etapa 3. Adapter propio en QuantOrchestrator

Construir una capa propia del repo, separada del SDK, con responsabilidades claras:

- `LighterMarketDataAdapter`
- `LighterExecutionAdapter`
- `LighterAccountAdapter`
- `LighterReconciliationJob`
- `RiskGate`
- `KillSwitch`

La regla debería ser:

- el SDK habla con Lighter,
- nuestra capa decide si una orden puede existir.

### Etapa 4. Controlled live trading

Solo después de tener:

- límites por estrategia,
- límites por cuenta,
- límites por instrumento,
- controles de notional,
- cancel-all de emergencia,
- reconciliación,
- alertas.

## Qué rol sí puede tener `lighter-agent-kit`

Rol recomendado:

- herramienta auxiliar de exploración,
- debugger operativo,
- interfaz humana rápida,
- soporte para playbooks.

Rol no recomendado:

- motor central de bots automáticos con fondos reales.

---

## 9. Checklist antes de usar con fondos reales

Antes de tocar dinero real, esta lista debería estar completa.

- [ ] Entendemos exactamente qué mercado de Lighter vamos a operar: spot, perp, margin mode, leverage, fees, funding.
- [ ] Validamos tick size, lot size, min size y reglas de cada símbolo.
- [ ] Las credenciales live están separadas de paper/dev.
- [ ] Ninguna clave está en el repo, ni en `.env` commiteado, ni en logs.
- [ ] El adapter propio persiste intents, órdenes, fills y estados.
- [ ] Tenemos reconciliación periódica entre estado interno y estado del exchange.
- [ ] Tenemos política de retry para writes que no pueda duplicar órdenes silenciosamente.
- [ ] Tenemos kill switch.
- [ ] Tenemos `cancel_all` probado operativamente.
- [ ] Tenemos límites por exposición, notional, pérdida diaria y drawdown.
- [ ] Tenemos monitoreo de websocket y fallback razonable ante desconexión.
- [ ] Probamos reinicio del proceso con órdenes abiertas.
- [ ] Probamos comportamiento ante timeout, error parcial y respuesta ambigua del exchange.
- [ ] Probamos un entorno pequeño de live con tamaño mínimo.
- [ ] Validamos manualmente un ciclo completo: enviar, modificar, cancelar, fill parcial, fill total, consulta histórica, PnL.
- [ ] El agente, si existe, no puede enviar órdenes live sin guardrails externos.
- [ ] La operatoria live requiere confirmación explícita o policy engine.
- [ ] Está claro quién rota credenciales y cómo se revocan.
- [ ] Revisamos supply chain de la versión exacta aprobada.
- [ ] Hay runbook de incidentes.

---

## 10. Próximos pasos

Recomendación concreta para QuantOrchestrator:

1. Documentar una versión aprobada de `lighter-python` y fijarla por commit o tag.
2. Hacer una validación manual controlada en entorno aislado:
   - solo lectura,
   - luego paper,
   - luego un smoke test live con tamaño mínimo.
3. Diseñar una capa propia de ejecución y riesgo encima del SDK.
4. Tratar `lighter-agent-kit` como herramienta secundaria, no como base arquitectónica.
5. Escribir un `adapter spec` interno para Lighter con:
   - market data,
   - order lifecycle,
   - account state,
   - reconciliation,
   - risk hooks.
6. Preparar una matriz de validación real:
   - happy path,
   - red parcial,
   - timeout,
   - order reject,
   - fill parcial,
   - reconexión,
   - replay/restart.
7. Recién después evaluar si vale la pena integrar algún flujo de agente para operación asistida.

---

## Anexo: comandos útiles y estado de verificación

## Verificados en fuentes públicas

Estos comandos o patrones están respaldados por README/repos públicos, aunque igual hay que probarlos localmente.

```bash
git clone https://github.com/elliottech/lighter-python.git
git clone https://github.com/elliottech/lighter-agent-kit.git
```

```bash
python -m pip install "git+https://github.com/elliottech/lighter-python.git@<commit>"
```

```bash
python vendor/lighter-agent-kit/scripts/query.py --help
python vendor/lighter-agent-kit/scripts/trade.py --help
python vendor/lighter-agent-kit/scripts/paper.py --help
```

```bash
export LIGHTER_API_PRIVATE_KEY="<valor>"
export LIGHTER_ACCOUNT_INDEX="<valor>"
export LIGHTER_API_KEY_INDEX="<valor>"
export LIGHTER_HOST="https://testnet.zklighter.elliot.ai"
```

## Requieren validación manual local

Estos puntos son razonables, pero hay que chequearlos en nuestro entorno antes de asumirlos como estables.

- que `requirements.lock` resuelva sin conflictos en nuestro sistema;
- que la sintaxis exacta de todos los subcomandos del agent kit siga igual;
- que el websocket del SDK se comporte bien bajo reconexión real;
- que paper trading sea suficientemente parecido al flujo que queremos probar;
- que la versión exacta aprobada no tenga regresiones en spot/perps;
- que la firma, nonce handling y write path se comporten bien ante fallos intermitentes.

---

## Cierre

La lectura honesta es esta:

- `lighter-python` sí puede ser un buen punto de partida técnico.
- `lighter-agent-kit` sí puede ser útil, pero como herramienta auxiliar.
- Si queremos bots serios en QuantOrchestrator, la responsabilidad real sigue siendo nuestra: arquitectura, riesgo, reconciliación, observabilidad y disciplina operacional.
