/**
 * Shown instead of the app when a required build-time variable is missing.
 *
 * A misconfigured build must explain itself in Persian: the alternative is a
 * blank page and a developer hunting through console output.
 */

import type { JSX } from "react";

// Styles for this notice live in auth.css, which main.tsx loads once globally.

interface ConfigNoticeProps {
  missing: string[];
}

export function ConfigNotice({ missing }: ConfigNoticeProps): JSX.Element {
  return (
    <div className="auth" dir="rtl" lang="fa">
      <section className="auth__card config-notice" role="alert">
        <p className="auth__eyebrow">دستیار هوشمند سازمانی</p>
        <h1 className="auth__title">پیکربندی ناقص است</h1>
        <p className="auth__text">
          برای ورود، این متغیرهای محیطی در فایل <code>.env</code> ریشهٔ پروژه تعریف شوند و بعد
          سرور توسعه دوباره اجرا شود:
        </p>

        <ul className="config-notice__list">
          {missing.map((name) => (
            <li key={name}>
              <code>{name}</code>
            </li>
          ))}
        </ul>

        <p className="auth__hint">
          مقادیر را از پنل Supabase (بخش Project Settings ← API) بردارید. کلید مورد استفاده
          «anon public» است؛ کلید <code>service_role</code> هرگز نباید در فرانت‌اند قرار بگیرد.
        </p>
      </section>
    </div>
  );
}
