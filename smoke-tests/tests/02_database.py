"""The schema and seed data the backend creates."""


def test_migrations_recorded(stack, detail):
    """Flyway migrations recorded"""
    count = int(stack.sql("select count(*) from schema_version"))
    detail(f"{count} migrations")
    assert count > 0


def test_countries_seeded(stack, detail):
    """Countries seeded from import/countries.csv"""
    count = int(stack.sql("select count(*) from Country"))
    detail(f"{count} countries")
    assert count == 249


def test_postgis_extension(stack, detail):
    """PostgreSQL deployments have PostGIS and unaccent enabled"""
    import pytest
    if getattr(stack, "spec", {}).get("database") != "postgresql":
        pytest.skip("PostGIS is only expected on PostgreSQL deployments")
    versions = stack.sql("SELECT extname || ' ' || extversion FROM pg_extension "
                         "WHERE extname IN ('postgis', 'unaccent') ORDER BY extname").splitlines()
    detail(", ".join(versions))
    assert any(v.startswith("postgis ") for v in versions)
    assert any(v.startswith("unaccent ") for v in versions)
