ALTER TABLE finance_month_plan
ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'finance_month_plan'::regclass
          AND conname = 'finance_month_plan_pkey'
          AND pg_get_constraintdef(oid) = 'PRIMARY KEY (month_start)'
    ) THEN
        ALTER TABLE finance_month_plan DROP CONSTRAINT finance_month_plan_pkey;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'finance_month_plan'::regclass
          AND conname = 'finance_month_plan_marketplace_month_start_pkey'
    ) THEN
        ALTER TABLE finance_month_plan
        ADD CONSTRAINT finance_month_plan_marketplace_month_start_pkey
        PRIMARY KEY (marketplace, month_start);
    END IF;
END $$;
