# Нормативный пакет модели dji-area-evidence-2026-09-08-final-1

Исследовательские документы лежат ВНЕ репозитория и не изменяются:
`C:\VehicleSoft_Astra_Blind\OUTPUT\FINAL` (владелец). Здесь -- копия манифеста
`LOCKED_SHA256_FINAL.json` и таблица прослеживаемости: какой нормативный
файл какой модуль реализует. Реализация проверена против него 2026-09-08
(11/11 SHA256 совпали до начала работы).

| Файл пакета | SHA256 (первые 16) | Реализовано в |
|---|---|---|
| `DJI_AREA_FINAL_ENGINEERING_VERDICT.md` | `58a016a86197602d` | docs/DJI_AREA_IMPLEMENTATION.md (границы, что не делается) |
| `VEHICLE_SOFT_DJI_AREA_PRODUCTION_SPEC.md` | `4a122b0abf95427b` | dji_area/{v4,resolver,structural,field,aggregate,store}.py, models.py (dji_*), migrate_dji_area_evidence_001.py |
| `VEHICLE_SOFT_DJI_AREA_ACCEPTANCE_TESTS.md` | `b6f02dd8c57038f7` | tests/test_dji_area_core.py, tests/test_dji_area_ingest_001.py |
| `ASTRA_CLAUDE_ADVERSARIAL_COMPARISON.md` | `a400a049a2eb5716` | supporting evidence |
| `MODEL_RECONCILIATION_TABLE.csv` | `4a4b36af2313da6e` | supporting evidence |
| `DJI_AREA_FINAL_EVIDENCE_MODEL.md` | `622c512152625bf4` | dji_area/v4.py (presence, baseline), dji_area/resolver.py (statuses) |
| `FINAL_METRIC_TAXONOMY.csv` | `d6cb952ddd4e1639` | models.py DjiAreaCalculation (колонки метрик, billable NULL) |
| `TRUE_HOLDOUT_PROTOCOL_LOCKED.md` | `7f1dc0daadac27d4` | docs/DJI_AREA_IMPLEMENTATION.md (квалификация площадки) |
| `WITHHELD_16_FINAL_RECONCILIATION.csv` | `e78471f0fa62caa9` | реплей: 16 withheld-кейсов |
| `AUGUST_REFERENCE_NUMBERS.json` | `7006b27d13c363ca` | реплей августа (сверка, не hardcode): DJI_AREA_IMPLEMENTATION_RESULT.md |
| `AUGUST_STALE_RECONCILIATION.csv` | `19769c87194f8a92` | реплей августа: 414 flat + 13 partial поимённо |

Проверка целостности пакета у владельца:

```powershell
Set-Location "C:\VehicleSoft_Astra_Blind"
& "C:\Program Files\Python314\python.exe" -c "import json,hashlib;m=json.load(open('OUTPUT/FINAL/LOCKED_SHA256_FINAL.json'));print(all(hashlib.sha256(open(p,'rb').read()).hexdigest()==h for p,h in m['sha256'].items()))"
```
