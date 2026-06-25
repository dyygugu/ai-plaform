from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_nas_compose_public_base_url_defaults_to_nas_address_but_allows_override() -> None:
    compose = (ROOT / "infra" / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "AIDP_PUBLIC_BASE_URL: ${AIDP_PUBLIC_BASE_URL:-http://192.168.10.149:8789}" in compose
    assert "AIDP_PUBLIC_BASE_URL: http://127.0.0.1:8789" not in compose
