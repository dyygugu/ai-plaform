$ErrorActionPreference = "Stop"
Push-Location (Join-Path $PSScriptRoot "..")
try {
$pythonCode = @"
import ast
from pathlib import Path
for p in list(Path('backend/app').rglob('*.py')) + list(Path('backend/alembic').rglob('*.py')) + list(Path('backend/tests').rglob('*.py')):
    ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
print('python_ast_ok=true')
"@
  $pythonCode | python -
  if ($LASTEXITCODE -ne 0) { throw "python_ast_failed" }
  Get-Content -Raw -Encoding UTF8 'frontend/package.json' | ConvertFrom-Json | Out-Null
  Get-Content -Raw -Encoding UTF8 'frontend/tsconfig.json' | ConvertFrom-Json | Out-Null
  docker compose -f 'infra/docker-compose.dev.yml' config --quiet
  $env:PYTHONDONTWRITEBYTECODE = "1"
  $env:PYTHONPATH = (Resolve-Path "backend").Path
  python backend/tests/api_smoke.py
  if ($LASTEXITCODE -ne 0) { throw "api_smoke_failed" }
  Write-Output 'p1_verify_ok=true'
}
finally {
  Pop-Location
}
