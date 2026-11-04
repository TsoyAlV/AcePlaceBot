"""User persistence and server-side administrative access checks."""

from dataclasses import dataclass

from psycopg import Connection


@dataclass(frozen=True)
class Client:
    user_id: int
    telegram_user_id: int
    language: str


def register_client(
    connection: Connection, *, telegram_user_id: int, telegram_chat_id: int,
    name: str, username: str | None, language_code: str | None,
) -> Client:
    default_language = 'ko' if (language_code or '').lower().startswith('ko') else 'ru'
    row = connection.execute(
        """INSERT INTO clients(telegram_user_id, telegram_chat_id, name, username, language)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (telegram_user_id) DO UPDATE SET
            telegram_chat_id=EXCLUDED.telegram_chat_id,
            name=EXCLUDED.name, username=EXCLUDED.username
        RETURNING user_id, telegram_user_id, language""",
        (telegram_user_id, telegram_chat_id, name, username, default_language),
    ).fetchone()
    return Client(*row)


def set_language(connection: Connection, telegram_user_id: int, language: str) -> None:
    if language not in ('ru', 'ko'):
        raise ValueError('Unsupported language')
    row = connection.execute(
        'UPDATE clients SET language=%s WHERE telegram_user_id=%s RETURNING user_id',
        (language, telegram_user_id),
    ).fetchone()
    if row is None:
        raise ValueError('User must register first')


def admin_role(connection: Connection, telegram_user_id: int) -> str | None:
    row = connection.execute(
        """SELECT a.role FROM admins a JOIN clients c ON c.user_id=a.user_id
        WHERE c.telegram_user_id=%s AND a.active=true
        AND a.role IN ('owner','admin','operator')""",
        (telegram_user_id,),
    ).fetchone()
    return row[0] if row else None


def grant_first_owner(connection: Connection, telegram_user_id: int, google_email: str | None) -> bool:
    """Local bootstrap only; never expose this function as a Telegram command."""
    if telegram_user_id <= 0:
        raise ValueError('Telegram ID must be positive')
    connection.execute('SELECT pg_advisory_xact_lock(836021471)')
    target = connection.execute(
        'SELECT user_id FROM clients WHERE telegram_user_id=%s', (telegram_user_id,),
    ).fetchone()
    if target is None:
        raise ValueError('User not found. Send /start to the test bot first.')
    owners = connection.execute("SELECT user_id FROM admins WHERE role='owner'").fetchall()
    if owners:
        if len(owners) == 1 and owners[0][0] == target[0]:
            return False
        raise ValueError('An owner already exists. Bootstrap cannot appoint another owner.')
    connection.execute(
        """INSERT INTO admins(user_id, google_email, role) VALUES (%s,%s,'owner')
        ON CONFLICT (user_id) DO UPDATE SET role='owner', active=true,
            google_email=COALESCE(EXCLUDED.google_email,admins.google_email)""",
        (target[0], google_email),
    )
    return True
