-- Support-ticket demo data for the SQLite connector.
--
-- This file is the source of truth for the demo database, so it is reviewable SQL in
-- the repository rather than rows created by a script at runtime. Apply it with:
--
--   apps/api/.venv/Scripts/python.exe apps/api/scripts/seed_support_sqlite.py
--
-- The seeder drops and recreates the table, so this file is safe to re-run and is
-- idempotent as a whole. The running application only ever opens the resulting file
-- with mode=ro; nothing in the app writes here.
--
-- Columns are deliberately minimal: a ticket row carries the owning customer, what the
-- customer asked about, its lifecycle state, and timestamps. There is no agent, no
-- internal note and no contact detail — the connector could not project them anyway,
-- and data that cannot leave the database has no reason to be in it.
--
-- customer_ref is the subject from the verified token; the demo uses the same three
-- demo subjects as the rest of the product ('alice-id', 'bob-id', 'carol-id').

CREATE TABLE IF NOT EXISTS support_tickets (
    customer_ref TEXT NOT NULL,
    ticket_id    TEXT NOT NULL,
    subject      TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('open', 'pending', 'closed')),
    priority     TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (customer_ref, ticket_id)
);

-- Alice: two open tickets, so the count tool has a non-trivial answer for the demo
-- user, plus one pending and one closed for a realistic mix.
INSERT INTO support_tickets (customer_ref, ticket_id, subject, description, status, priority, created_at, updated_at) VALUES
    ('alice-id', 'TKT-1042', 'خطا در ورود به حساب کاربری',
     'از دیروز هنگام ورود پیام «نام کاربری یا گذرواژه نامعتبر است» نمایش داده می‌شود؛ گذرواژه را نیز بازنشانی کردم اما مشکل ادامه دارد.',
     'open', 'high', '2026-01-12T09:14:00+03:30', '2026-02-03T11:26:00+03:30'),
    ('alice-id', 'TKT-1043', 'درخواست صدور فاکتور رسمی',
     'برای خرید شماره ۴۸۲۹۱ نیاز به فاکتور رسمی با کد اقتصادی دارم. لطفاً فاکتور را به ایمیل سازمانی من ارسال کنید.',
     'open', 'normal', '2026-01-19T15:40:00+03:30', '2026-01-29T10:05:00+03:30'),
    ('alice-id', 'TKT-1044', 'تغییر شماره تماس حساب',
     'شماره تلفن همراه من تغییر کرده است. امکان ویرایش آن از پنل کاربری وجود ندارد و پیام خطا می‌دهد.',
     'pending', 'low', '2026-01-05T12:30:00+03:30', '2026-01-22T16:48:00+03:30'),
    ('alice-id', 'TKT-1045', 'مشکل در پرداخت آنلاین',
     'مبلغ از حساب کسر شد اما سفارش ثبت نشد. پس از دو روز مبلغ به حساب بازگشت.',
     'closed', 'high', '2025-12-11T08:55:00+03:30', '2025-12-16T14:20:00+03:30');

-- Bob: a mix of states, including one open ticket about something only support can fix.
INSERT INTO support_tickets (customer_ref, ticket_id, subject, description, status, priority, created_at, updated_at) VALUES
    ('bob-id', 'TKT-2011', 'بازنشانی رمز عبور انجام نشد',
     'لینک بازنشانی رمز عبور را دریافت نمی‌کنم؛ پوشه هرزنامه را هم بررسی کردم. دسترسی به ایمیل جایگزین ندارم.',
     'open', 'high', '2026-01-24T18:02:00+03:30', '2026-02-01T09:12:00+03:30'),
    ('bob-id', 'TKT-2012', 'تأخیر در ارسال سفارش',
     'سفارش ۷۷۳۱ قرار بود سه‌شنبه تحویل شود اما وضعیت پیگیری از پنج روز پیش تغییر نکرده است.',
     'pending', 'high', '2026-01-15T11:08:00+03:30', '2026-01-27T13:35:00+03:30'),
    ('bob-id', 'TKT-2013', 'استعلام موجودی انبار',
     'برای سفارش سازمانی نیاز دارم بدانم موجودی کالای مورد نظر تا پایان ماه چه میزان است.',
     'closed', 'low', '2025-11-29T10:17:00+03:30', '2025-12-04T12:00:00+03:30'),
    ('bob-id', 'TKT-2014', 'درخواست لغو اشتراک سالانه',
     'قصد تمدید اشتراک را ندارم. لطفاً شرایط لغو و بازگشت وجه باقیمانده دوره را اعلام کنید.',
     'open', 'low', '2026-02-02T09:45:00+03:30', '2026-02-04T08:30:00+03:30');

-- Carol: one of each state, exercising the three status values in a second tenant-less
-- demo subject.
INSERT INTO support_tickets (customer_ref, ticket_id, subject, description, status, priority, created_at, updated_at) VALUES
    ('carol-id', 'TKT-3087', 'ایمیل اطلاع‌رسانی دریافت نمی‌شود',
     'از زمان تغییر دامنه سازمانی، هیچ‌یک از ایمیل‌های اطلاع‌رسانی به من نمی‌رسد. تنظیمات دامنه را بررسی کرده‌ایم.',
     'pending', 'normal', '2026-01-18T14:22:00+03:30', '2026-01-31T17:05:00+03:30'),
    ('carol-id', 'TKT-3088', 'خطا در بارگذاری فایل پیوست',
     'هنگام ارسال یک فایل ۱۲ مگابایتی، بارگذاری در نیمه راه متوقف می‌شود و پیام «خطای سرور» نمایش داده می‌شود.',
     'open', 'high', '2026-02-01T10:50:00+03:30', '2026-02-05T09:18:00+03:30'),
    ('carol-id', 'TKT-3089', 'پرسش درباره شرایط گارانتی',
     'دوره گارانتی محصول خریداری‌شده چند ماه است و آیا شامل قطعات مصرفی هم می‌شود؟',
     'closed', 'normal', '2025-12-20T13:11:00+03:30', '2025-12-23T15:44:00+03:30'),
    ('carol-id', 'TKT-3090', 'درخواست همکاری سازمانی',
     'شرکت ما به دنبال خرید پلن سازمانی برای حدود ۴۰ کاربر است. برای گفتگو با تیم فروش درخواست تماس داریم.',
     'pending', 'low', '2026-01-09T16:35:00+03:30', '2026-01-21T11:02:00+03:30');
