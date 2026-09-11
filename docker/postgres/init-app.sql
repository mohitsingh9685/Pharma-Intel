-- Runs only when the PostgreSQL volume is initialized for the first time.
-- psql quoting treats names as identifiers and passwords as literal values.
\set ON_ERROR_STOP on
\getenv app_db_name APP_DB_NAME
\getenv app_db_user APP_DB_USER
\getenv app_db_password APP_DB_PASSWORD

CREATE ROLE :"app_db_user"
    LOGIN PASSWORD :'app_db_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

CREATE DATABASE :"app_db_name" OWNER :"app_db_user";
REVOKE ALL ON DATABASE :"app_db_name" FROM PUBLIC;

-- The application role owns only this development database so it can run
-- migrations. Production runtime and migration permissions will be separated.
