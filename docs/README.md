# QuantOrchestrator Docs

Indice general de documentacion del proyecto. Esta carpeta separa especificaciones operables, research y auditorias para evitar que `docs/` sea una carpeta plana de papers.

## Estrategias

- [IDV BTCUSDT 15m - especificacion funcional](strategies/idv-btcusdt-15m-spec.md)

## Research

- [Seleccion de estrategias intradiarias](research/intraday-strategy-selection.md)

## Integraciones

- [Auditoria de integracion Lighter](integrations/lighter-integration-audit.md)

## Papers y extracts

Los PDFs, extracts y textos derivados de papers estan en [`papers/`](papers/). Esa carpeta funciona como biblioteca de referencia, no como especificacion de implementacion.

## Regla de uso

- Las specs que puedan convertirse en Pine, Python o bots van en `strategies/`.
- Las sintesis, comparativas y decisiones de investigacion van en `research/`.
- Las auditorias tecnicas de venues, SDKs o conectores van en `integrations/`.
- Los papers originales y sus extracts van en `papers/`.
