# Local database testing

Django creates a temporary database, runs migrations and tests inside it, then
removes it. Creating that database needs a PostgreSQL privilege the application
role intentionally does not have.

Local tests therefore use `pharma_test_runner`. This role can create disposable
databases but is not a superuser and cannot create roles. Application requests
continue to use the restricted `pharma_app` role.

Configure the role once:

```bash
python scripts/configure_local_test_database.py
```

Run database-backed tests with the test settings:

```bash
python manage.py test --settings=config.test_settings
```

The test role and password are local secrets in `.env`; they are never committed.
