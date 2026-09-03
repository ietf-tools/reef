# Copyright The IETF Trust 2026, All Rights Reserved
"""Swap DocumentSet's sequential primary key for a random UUID.

Hand-written, because the field Django generates for this alter is
``ALTER COLUMN id TYPE uuid USING id::uuid``, and Postgres rejects that cast
outright: there is no bigint-to-uuid conversion, so it fails on an empty table
as well as a full one. The autodetector also misses the foreign key in
subscriptions, leaving that column a bigint pointing at a uuid.

So the swap is done by hand, alongside rather than in place: a new column is
filled, the children are repointed through the old key while it still exists,
and only then is the old key dropped. Rows and their relationships survive.

Constraint and index names are spelled out because they are Django's own
generated names, and the tables have to come out of this migration named
exactly as Django expects to find them.
"""

import uuid

from django.db import migrations, models

FORWARD = """
-- The parent gets its uuid first, one distinct value per row. A column
-- default would not do: it is evaluated once for the ALTER and every existing
-- row would come out holding the same id.
ALTER TABLE docsets_documentset ADD COLUMN uuid_id uuid;
UPDATE docsets_documentset SET uuid_id = gen_random_uuid();
ALTER TABLE docsets_documentset ALTER COLUMN uuid_id SET NOT NULL;

-- The children are repointed through the old integer key, which is still
-- there and still joins.
ALTER TABLE docsets_documentsetentry ADD COLUMN uuid_document_set_id uuid;
UPDATE docsets_documentsetentry AS child SET uuid_document_set_id = parent.uuid_id
    FROM docsets_documentset AS parent WHERE child.document_set_id = parent.id;
ALTER TABLE docsets_documentsetentry ALTER COLUMN uuid_document_set_id SET NOT NULL;

-- Left nullable: only the set kind of subscription carries one.
ALTER TABLE subscriptions_subscription ADD COLUMN uuid_document_set_id uuid;
UPDATE subscriptions_subscription AS child SET uuid_document_set_id = parent.uuid_id
    FROM docsets_documentset AS parent WHERE child.document_set_id = parent.id;

-- Everything naming the old columns has to go before they can be dropped. The
-- uniques are dropped explicitly rather than left to cascade, so that putting
-- them back below is visibly this migration's job.
ALTER TABLE docsets_documentsetentry
    DROP CONSTRAINT docsets_documentsete_document_set_id_f78ebbe0_fk_docsets_d;
ALTER TABLE docsets_documentsetentry DROP CONSTRAINT unique_document_per_set;
ALTER TABLE subscriptions_subscription
    DROP CONSTRAINT subscriptions_subscr_document_set_id_24639c9e_fk_docsets_d;
ALTER TABLE subscriptions_subscription DROP CONSTRAINT unique_subscription_per_user;
ALTER TABLE docsets_documentset DROP CONSTRAINT docsets_documentset_pkey;

ALTER TABLE docsets_documentsetentry DROP COLUMN document_set_id;
ALTER TABLE subscriptions_subscription DROP COLUMN document_set_id;
-- Takes the identity sequence with it.
ALTER TABLE docsets_documentset DROP COLUMN id;

ALTER TABLE docsets_documentset RENAME COLUMN uuid_id TO id;
ALTER TABLE docsets_documentsetentry
    RENAME COLUMN uuid_document_set_id TO document_set_id;
ALTER TABLE subscriptions_subscription
    RENAME COLUMN uuid_document_set_id TO document_set_id;

ALTER TABLE docsets_documentset
    ADD CONSTRAINT docsets_documentset_pkey PRIMARY KEY (id);
ALTER TABLE docsets_documentsetentry
    ADD CONSTRAINT docsets_documentsete_document_set_id_f78ebbe0_fk_docsets_d
    FOREIGN KEY (document_set_id) REFERENCES docsets_documentset(id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE docsets_documentsetentry
    ADD CONSTRAINT unique_document_per_set UNIQUE (document_set_id, doc);
ALTER TABLE subscriptions_subscription
    ADD CONSTRAINT subscriptions_subscr_document_set_id_24639c9e_fk_docsets_d
    FOREIGN KEY (document_set_id) REFERENCES docsets_documentset(id)
    DEFERRABLE INITIALLY DEFERRED;
-- NULLS NOT DISTINCT, as the model declares: document_set is null for every
-- kind but set, and Postgres would otherwise count those nulls as distinct and
-- stop the constraint blocking duplicates of the other kinds.
ALTER TABLE subscriptions_subscription
    ADD CONSTRAINT unique_subscription_per_user
    UNIQUE NULLS NOT DISTINCT (user_id, kind, params, document_set_id);

-- Dropping the columns dropped the indexes Django puts on a foreign key.
CREATE INDEX docsets_documentsetentry_document_set_id_f78ebbe0
    ON docsets_documentsetentry (document_set_id);
CREATE INDEX subscriptions_subscription_document_set_id_24639c9e
    ON subscriptions_subscription (document_set_id);
"""

# Going back invents new sequential ids rather than recovering the old ones,
# which are gone for good once this has run forwards. Relationships are kept,
# so a rollback is sound; the ids in anyone's saved URLs are not.
BACKWARD = """
ALTER TABLE docsets_documentset ADD COLUMN bigint_id bigint;
CREATE SEQUENCE docsets_documentset_id_seq OWNED BY docsets_documentset.bigint_id;
UPDATE docsets_documentset SET bigint_id = nextval('docsets_documentset_id_seq');
ALTER TABLE docsets_documentset ALTER COLUMN bigint_id SET NOT NULL;

ALTER TABLE docsets_documentsetentry ADD COLUMN bigint_document_set_id bigint;
UPDATE docsets_documentsetentry AS child SET bigint_document_set_id = parent.bigint_id
    FROM docsets_documentset AS parent WHERE child.document_set_id = parent.id;
ALTER TABLE docsets_documentsetentry ALTER COLUMN bigint_document_set_id SET NOT NULL;

ALTER TABLE subscriptions_subscription ADD COLUMN bigint_document_set_id bigint;
UPDATE subscriptions_subscription AS child SET bigint_document_set_id = parent.bigint_id
    FROM docsets_documentset AS parent WHERE child.document_set_id = parent.id;

ALTER TABLE docsets_documentsetentry
    DROP CONSTRAINT docsets_documentsete_document_set_id_f78ebbe0_fk_docsets_d;
ALTER TABLE docsets_documentsetentry DROP CONSTRAINT unique_document_per_set;
ALTER TABLE subscriptions_subscription
    DROP CONSTRAINT subscriptions_subscr_document_set_id_24639c9e_fk_docsets_d;
ALTER TABLE subscriptions_subscription DROP CONSTRAINT unique_subscription_per_user;
ALTER TABLE docsets_documentset DROP CONSTRAINT docsets_documentset_pkey;

ALTER TABLE docsets_documentsetentry DROP COLUMN document_set_id;
ALTER TABLE subscriptions_subscription DROP COLUMN document_set_id;
ALTER TABLE docsets_documentset DROP COLUMN id;

ALTER TABLE docsets_documentset RENAME COLUMN bigint_id TO id;
ALTER TABLE docsets_documentsetentry
    RENAME COLUMN bigint_document_set_id TO document_set_id;
ALTER TABLE subscriptions_subscription
    RENAME COLUMN bigint_document_set_id TO document_set_id;

ALTER TABLE docsets_documentset
    ALTER COLUMN id SET DEFAULT nextval('docsets_documentset_id_seq');
ALTER TABLE docsets_documentset
    ADD CONSTRAINT docsets_documentset_pkey PRIMARY KEY (id);
ALTER TABLE docsets_documentsetentry
    ADD CONSTRAINT docsets_documentsete_document_set_id_f78ebbe0_fk_docsets_d
    FOREIGN KEY (document_set_id) REFERENCES docsets_documentset(id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE docsets_documentsetentry
    ADD CONSTRAINT unique_document_per_set UNIQUE (document_set_id, doc);
ALTER TABLE subscriptions_subscription
    ADD CONSTRAINT subscriptions_subscr_document_set_id_24639c9e_fk_docsets_d
    FOREIGN KEY (document_set_id) REFERENCES docsets_documentset(id)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE subscriptions_subscription
    ADD CONSTRAINT unique_subscription_per_user
    UNIQUE NULLS NOT DISTINCT (user_id, kind, params, document_set_id);

CREATE INDEX docsets_documentsetentry_document_set_id_f78ebbe0
    ON docsets_documentsetentry (document_set_id);
CREATE INDEX subscriptions_subscription_document_set_id_24639c9e
    ON subscriptions_subscription (document_set_id);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("docsets", "0002_alter_documentset_visibility"),
        # The swap rewrites subscriptions' foreign key column, so that table has
        # to be finished before this runs.
        (
            "subscriptions",
            "0004_remove_subscription_unique_subscription_per_user_and_more",
        ),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(sql=FORWARD, reverse_sql=BACKWARD),
            ],
            # What the SQL above adds up to, in the terms Django tracks. The
            # foreign keys need no state change: they name the model, and their
            # column type follows whatever its primary key is.
            state_operations=[
                migrations.AlterField(
                    model_name="documentset",
                    name="id",
                    field=models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
            ],
        ),
    ]
