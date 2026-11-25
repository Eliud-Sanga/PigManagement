from django.db import migrations, models


TRIGGER = 'bizmanager_user_nonce_guard'
FUNCTION = 'bizmanager_user_nonce_guard_fn'


def invalidate_preexisting_sessions(apps, schema_editor):
    # Old sessions could have survived an earlier deactivation. Invalidate all
    # of them once during the initial migration, in the same DB transaction.
    user = apps.get_model('users', 'User')
    q = schema_editor.quote_name
    table = q(user._meta.db_table)
    nonce = q(user._meta.get_field('session_revocation_nonce').column)
    if schema_editor.connection.vendor == 'sqlite':
        schema_editor.execute(f'UPDATE {table} SET {nonce} = lower(hex(randomblob(16)))')
    elif schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(f'UPDATE {table} SET {nonce} = pg_catalog.gen_random_uuid()')
    else:
        raise RuntimeError('Initial session revocation supports SQLite and PostgreSQL only.')


def install_guard(apps, schema_editor):
    user = apps.get_model('users', 'User')
    q = schema_editor.quote_name
    table = q(user._meta.db_table)
    pk = q(user._meta.pk.column)
    active = q(user._meta.get_field('is_active').column)
    nonce = q(user._meta.get_field('session_revocation_nonce').column)
    trigger = q(TRIGGER)
    if schema_editor.connection.vendor == 'sqlite':
        schema_editor.execute(
            f'CREATE TRIGGER {trigger} AFTER UPDATE OF {active} ON {table} '
            f'FOR EACH ROW WHEN OLD.{active} = 1 AND NEW.{active} = 0 '
            f'AND NEW.{nonce} IS OLD.{nonce} BEGIN '
            f'UPDATE {table} SET {nonce} = lower(hex(randomblob(16))) WHERE {pk} = NEW.{pk}; END'
        )
    elif schema_editor.connection.vendor == 'postgresql':
        function = q(FUNCTION)
        schema_editor.execute(
            f'CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ '
            f'BEGIN IF OLD.{active} = TRUE AND NEW.{active} = FALSE '
            f'AND NEW.{nonce} IS NOT DISTINCT FROM OLD.{nonce} THEN '
            f'NEW.{nonce} := pg_catalog.gen_random_uuid(); END IF; RETURN NEW; END; $$'
        )
        schema_editor.execute(
            f'CREATE TRIGGER {trigger} BEFORE UPDATE OF {active} ON {table} '
            f'FOR EACH ROW EXECUTE FUNCTION {function}()'
        )
    else:
        raise RuntimeError('Session revocation guard supports SQLite and PostgreSQL only.')


def remove_guard(apps, schema_editor):
    user = apps.get_model('users', 'User')
    q = schema_editor.quote_name
    table, trigger = q(user._meta.db_table), q(TRIGGER)
    if schema_editor.connection.vendor == 'sqlite':
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {trigger}')
    elif schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute(f'DROP TRIGGER IF EXISTS {trigger} ON {table}')
        schema_editor.execute(f'DROP FUNCTION IF EXISTS {q(FUNCTION)}()')
    else:
        raise RuntimeError('Session revocation guard supports SQLite and PostgreSQL only.')


class Migration(migrations.Migration):
    dependencies = [('users', '0005_googleidentity')]
    operations = [
        migrations.AddField(
            model_name='user',
            name='session_revocation_nonce',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(invalidate_preexisting_sessions),
        migrations.RunPython(install_guard, remove_guard),
    ]
