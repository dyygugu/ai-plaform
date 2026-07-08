import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_nas_compose_public_base_url_defaults_to_public_platform_but_allows_override() -> None:
    compose = (ROOT / "infra" / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "AIDP_PUBLIC_BASE_URL: ${AIDP_PUBLIC_BASE_URL:-https://platform.51gugu.uk}" in compose
    assert "AIDP_PUBLIC_BASE_URL: http://127.0.0.1:8789" not in compose


def test_nas_compose_passes_public_auth_tokens_from_env_file() -> None:
    compose = (ROOT / "infra" / "docker-compose.dev.yml").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "AIDP_AUTH_ENABLED: ${AIDP_AUTH_ENABLED:-true}" in compose
    assert "AIDP_ADMIN_API_TOKEN: ${AIDP_ADMIN_API_TOKEN:-}" in compose
    assert "AIDP_BROWSER_EXTENSION_API_TOKEN: ${AIDP_BROWSER_EXTENSION_API_TOKEN:-}" in compose
    assert "AIDP_WEB_LOGIN_PHONE: ${AIDP_WEB_LOGIN_PHONE:-}" in compose
    assert "AIDP_WEB_LOGIN_PASSWORD_HASH: ${AIDP_WEB_LOGIN_PASSWORD_HASH:-}" in compose
    assert "pbkdf2_sha256:<iterations>:<base64url_salt>:<base64url_digest>" in env_example
    assert "旧 pbkdf2_sha256$... 格式仅兼容读取" in env_example
    assert "AIDP_WEB_SESSION_SECRET: ${AIDP_WEB_SESSION_SECRET:-}" in compose
    assert "AIDP_WEB_SESSION_TTL_SECONDS: ${AIDP_WEB_SESSION_TTL_SECONDS:-604800}" in compose
    assert "AIDP_TRUSTED_PROXY_CIDRS: ${AIDP_TRUSTED_PROXY_CIDRS:-}" in compose
    assert "AIDP_LEGACY_PRODUCTION_ROUTES_ENABLED: ${AIDP_LEGACY_PRODUCTION_ROUTES_ENABLED:-false}" in compose


def test_nas_compose_and_env_example_keep_api_prefix_visible_to_frontend() -> None:
    compose = (ROOT / "infra" / "docker-compose.dev.yml").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "AIDP_API_PREFIX: ${AIDP_API_PREFIX:-/api/v1}" in compose
    assert "AIDP_API_PREFIX=/api/v1" in env_example
    assert "VITE_AIDP_API_PREFIX=/api/v1" in env_example


def test_frontend_nginx_cleans_spoofable_forwarded_headers() -> None:
    nginx = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "location = /aidp-runtime-config.js" in nginx
    assert "proxy_pass http://api:8787/aidp-runtime-config.js;" in nginx
    assert "location ^~ /api/" in nginx
    assert "location ^~ /api/v1/" in nginx
    assert "try_files $uri $uri/ /index.html;" in nginx
    assert "proxy_pass http://api:8787$request_uri;" in nginx
    assert "proxy_set_header X-AIDP-Client-IP $remote_addr;" in nginx
    assert 'proxy_set_header X-Forwarded-For "";' in nginx
    assert 'proxy_set_header CF-Connecting-IP "";' in nginx
    assert "location @aidp_backend" not in nginx
    assert "location ~ ^/(api|custom-api)(/|$)" not in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx


def test_frontend_docker_generates_nginx_proxy_for_runtime_api_prefix() -> None:
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "frontend" / "docker-entrypoint.d" / "10-render-aidp-nginx.sh").read_text(encoding="utf-8")

    assert "ENV AIDP_API_PREFIX=/api/v1" in dockerfile
    assert "10-render-aidp-nginx.sh" in dockerfile
    assert 'api_prefix="${AIDP_API_PREFIX:-/api/v1}"' in entrypoint
    assert "sed 's#//*#/#g; s#/*$##'" in entrypoint
    assert 'write_proxy_locations "$api_prefix"' in entrypoint
    assert 'write_proxy_locations "/api"' in entrypoint
    assert "try_files $uri $uri/ /index.html;" in entrypoint
    assert "location @aidp_backend" not in entrypoint
    assert 'proxy_set_header X-Forwarded-For "";' in entrypoint
    assert 'proxy_set_header CF-Connecting-IP "";' in entrypoint


def test_backend_dockerfile_disables_uvicorn_proxy_header_rewrite() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "--no-proxy-headers" in dockerfile


def test_custom_api_prefix_is_not_hardcoded_in_auxiliary_surfaces() -> None:
    workers_page = (ROOT / "frontend" / "src" / "pages" / "WorkersPage.tsx").read_text(encoding="utf-8")
    release_schema = (ROOT / "backend" / "app" / "schemas" / "execution_devices.py").read_text(encoding="utf-8")
    helper_readme = (ROOT / "local-agent-source" / "README.md").read_text(encoding="utf-8")
    run_acceptance = (ROOT / "scripts" / "run-acceptance.ps1").read_text(encoding="utf-8")
    docker_smoke = (ROOT / "scripts" / "docker-smoke.ps1").read_text(encoding="utf-8")
    seed_local_sample = (ROOT / "scripts" / "seed-local-sample.ps1").read_text(encoding="utf-8")

    assert "apiPrefix" in workers_page
    assert '<Descriptions.Item label="平台地址">/api/v1</Descriptions.Item>' not in workers_page
    assert 'download_url="/api/v1/local-agent' not in release_schema
    assert "/api/v1/operation-recordings" not in helper_readme
    assert "$BaseUrl/api/v1" not in run_acceptance
    assert "$BaseUrl/api/v1" not in docker_smoke
    assert "$BaseUrl/api/v1" not in seed_local_sample


def test_local_acceptance_scripts_send_auth_header_to_protected_apis() -> None:
    run_acceptance = (ROOT / "scripts" / "run-acceptance.ps1").read_text(encoding="utf-8")
    docker_smoke = (ROOT / "scripts" / "docker-smoke.ps1").read_text(encoding="utf-8")
    seed_local_sample = (ROOT / "scripts" / "seed-local-sample.ps1").read_text(encoding="utf-8")

    for script in (run_acceptance, docker_smoke, seed_local_sample):
        assert "[string]$ApiToken" in script
        assert "AIDP_ADMIN_API_TOKEN" in script
        assert "Get-ApiHeaders" in script
        assert "$ApiHeaders" in script

    assert "& (Join-Path $PSScriptRoot \"docker-smoke.ps1\") -BaseUrl $BaseUrl -ApiPrefix $ApiPrefix -ApiToken $ApiToken -SeedSample" in run_acceptance
    assert "& (Join-Path $PSScriptRoot \"seed-local-sample.ps1\") -BaseUrl $BaseUrl -ApiPrefix $ApiPrefix -ApiToken $ApiToken" in docker_smoke
    assert '"X-AIDP-API-Token"' in seed_local_sample


def test_user_visible_reports_follow_runtime_api_prefix() -> None:
    service_paths = [
        ROOT / "backend" / "app" / "services" / "api_paths.py",
        ROOT / "backend" / "app" / "services" / "delivery_service.py",
        ROOT / "backend" / "app" / "services" / "incident_service.py",
        ROOT / "backend" / "app" / "services" / "ops_job_service.py",
        ROOT / "backend" / "app" / "services" / "score_loop_service.py",
        ROOT / "backend" / "app" / "services" / "final_acceptance_service.py",
        ROOT / "backend" / "app" / "services" / "inspection_service.py",
        ROOT / "backend" / "app" / "services" / "freeze_service.py",
        ROOT / "backend" / "app" / "services" / "data_quality_service.py",
    ]

    for path in service_paths:
        text = path.read_text(encoding="utf-8")
        assert "_api_path(" in text or "api_path(" in text, path
        assert " | None" not in text, path
        assert '"/api/v1' not in text, path
        assert '}/api/v1' not in text, path
        assert " /api/v1/" not in text, path
        assert "manage.51gugu.uk/api/v1" not in text, path


def test_local_helper_console_opens_existing_workers_route() -> None:
    helper = (ROOT / "local-agent-source" / "host-launcher.ps1").read_text(encoding="utf-8")

    assert "+ '/workers'" in helper
    assert "/execution-devices" not in helper


def test_bon8_rejudge_timer_event_url_uses_runtime_api_prefix() -> None:
    script = (ROOT / "scripts" / "rejudge-bon8-item-with-ai.py").read_text(encoding="utf-8")

    assert "AIDP_MONITOR_BASE_URL" in script
    assert "AIDP_API_PREFIX" in script
    assert "X-AIDP-API-Token" in script
    assert "ai-timer/events" in script
    assert "http://127.0.0.1:8789/api/v1/ai-timer/events" not in script


def test_bon8_rejudge_timer_helpers_normalize_prefix_and_auth(monkeypatch) -> None:
    module = _load_rejudge_script_module()
    monkeypatch.setenv("AIDP_MONITOR_BASE_URL", "https://platform.51gugu.uk/")
    monkeypatch.setenv("AIDP_API_PREFIX", "custom//api/")
    monkeypatch.setenv("AIDP_MONITOR_API_TOKEN", "monitor-token")
    monkeypatch.setenv("AIDP_ADMIN_API_TOKEN", "admin-token")
    monkeypatch.setenv("AIDP_API_TOKEN", "api-token")

    assert module._monitor_api_url("/ai-timer/events") == "https://platform.51gugu.uk/custom/api/ai-timer/events"
    assert module._monitor_api_headers() == {"X-AIDP-API-Token": "monitor-token"}

    monkeypatch.delenv("AIDP_MONITOR_API_TOKEN")
    assert module._monitor_api_headers() == {"X-AIDP-API-Token": "admin-token"}

    monkeypatch.setenv("AIDP_MONITOR_BASE_URL", "https://platform.51gugu.uk/api/v1/")
    monkeypatch.setenv("AIDP_API_PREFIX", "/api/v1")
    assert module._monitor_api_url("ai-timer/events") == "https://platform.51gugu.uk/api/v1/ai-timer/events"


def _load_rejudge_script_module():
    module_name = "_aidp_rejudge_bon8_item_with_ai_static_test"
    if module_name in sys.modules:
        return sys.modules[module_name]
    script_path = ROOT / "scripts" / "rejudge-bon8-item-with-ai.py"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise AssertionError("failed to load rejudge script")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
