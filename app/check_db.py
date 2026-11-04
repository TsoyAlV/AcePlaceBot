"""Run with: python -m app.check_db."""

from app.config import load_settings
from app.database import check_database


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as error:
        print(str(error))
        return 1
    try:
        ready = check_database(settings)
    except Exception as error:
        # Connection exceptions may contain credentials or connection details.
        print(f"PostgreSQL check failed ({type(error).__name__}).")
        print("Check Docker, POSTGRES_HOST/PORT and credentials in .env.")
        return 1
    print("PostgreSQL: OK" if ready else "PostgreSQL: unexpected response")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
