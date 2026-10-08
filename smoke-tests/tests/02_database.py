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
