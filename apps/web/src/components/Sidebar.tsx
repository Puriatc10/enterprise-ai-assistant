import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";

import type { Conversation } from "../services/api";

interface SidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  /** Rename a thread; the sidebar closes its editor as soon as this settles. */
  onRename: (id: string, title: string) => Promise<void>;
  /** Delete a thread. The caller owns the confirmation copy. */
  onDelete: (id: string) => Promise<void>;
  onCreate: () => void;
  isCreating: boolean;
  loading: boolean;
  email: string | null;
  onSignOut: () => void;
  /** Mobile drawer state. Desktop keeps the column permanently visible. */
  open: boolean;
  onClose: () => void;
}

const TIME_FORMAT = new Intl.DateTimeFormat("fa-IR", { hour: "2-digit", minute: "2-digit" });
const DATE_FORMAT = new Intl.DateTimeFormat("fa-IR", { day: "numeric", month: "short" });

/**
 * Today shows a clock, anything older shows a date: the question a reader has
 * in this column is "is this the thread from this morning?".
 */
function formatStamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  if (parsed.toDateString() === new Date().toDateString()) return TIME_FORMAT.format(parsed);
  return DATE_FORMAT.format(parsed);
}

function PlusIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" aria-hidden="true">
      <path d="M10 4.5v11M4.5 10h11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function CloseIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" aria-hidden="true">
      <path d="M5.5 5.5l9 9m0-9l-9 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

function SignOutIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" aria-hidden="true">
      <path
        d="M11.5 4.5H6.8c-.7 0-1.3.6-1.3 1.3v8.4c0 .7.6 1.3 1.3 1.3h4.7"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path d="M13 10h4.2M15.2 7.6L17.7 10l-2.5 2.4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Sidebar(props: SidebarProps): JSX.Element {
  const {
    conversations,
    activeId,
    onSelect,
    onRename,
    onDelete,
    onCreate,
    isCreating,
    loading,
    email,
    onSignOut,
    open,
    onClose,
  } = props;

  // Row-level UI state. Kept here rather than in the hook: which menu is open is not
  // part of the conversation model, and it must die with the sidebar.
  const [menuId, setMenuId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const committingRef = useRef<string | null>(null);

  const commitRename = async (id: string): Promise<void> => {
    // Enter also blurs the input, so without this guard one edit calls the API twice.
    if (committingRef.current === id) return;
    committingRef.current = id;
    const next = draft.trim();
    setRenamingId(null);
    if (next.length === 0) {
      committingRef.current = null;
      return;
    }
    setBusyId(id);
    try {
      await onRename(id, next);
    } catch {
      // The hook reports failures in the banner and rolls its own state back; the row
      // keeps its previous title, which is the truth.
    } finally {
      setBusyId(null);
      committingRef.current = null;
    }
  };

  const commitDelete = async (id: string): Promise<void> => {
    setBusyId(id);
    try {
      await onDelete(id);
    } catch {
      // Same contract as rename.
    } finally {
      setBusyId(null);
    }
  };

  // A menu that survives a click elsewhere looks like a stuck UI; close it on any
  // pointer press outside a row.
  useEffect(() => {
    if (menuId === null) return;
    const onPointerDown = (event: PointerEvent): void => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".conv-row")) return;
      setMenuId(null);
      setConfirmingId(null);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [menuId]);
  const closeRef = useRef<HTMLButtonElement | null>(null);

  // Escape closes the drawer from anywhere: a keyboard user inside the list
  // should not have to tab back to the close button to get out.
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onClose]);

  // Focus follows the drawer in, so the next Tab lands inside it rather than
  // continuing through the page behind. Deferred by a frame because the drawer
  // flips from visibility:hidden in the same commit, and an element that is
  // still hidden silently refuses focus. Nothing traps focus: Tab still leaves.
  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => {
      closeRef.current?.focus();
    });
    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [open]);

  return (
    <>
      <aside id="conversations" className={open ? "sidebar sidebar--open" : "sidebar"} aria-label="گفتگوها">
        <div className="sidebar__head">
          <p className="sidebar__brand">
            <span className="sidebar__mark" aria-hidden="true">
              <svg viewBox="0 0 20 20" width="18" height="18" fill="none">
                <path
                  d="M3.5 9.2c0-3 2.9-5.4 6.5-5.4s6.5 2.4 6.5 5.4-2.9 5.4-6.5 5.4c-.7 0-1.4-.1-2-.2L4.6 15.6l.7-2.6C4.2 12.1 3.5 10.8 3.5 9.2Z"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            دستیار سازمانی
          </p>
          <button
            ref={closeRef}
            type="button"
            className="sidebar__close"
            aria-label="بستن فهرست گفتگوها"
            onClick={onClose}
          >
            <CloseIcon />
          </button>
        </div>

        <button type="button" className="sidebar__new" onClick={onCreate} disabled={isCreating}>
          <PlusIcon />
          <span>{isCreating ? "در حال ساخت…" : "گفتگوی جدید"}</span>
        </button>

        <nav className="sidebar__nav" aria-label="فهرست گفتگوها">
          {loading ? (
            <>
              <p className="sr-only" role="status">
                در حال بارگذاری گفتگوها…
              </p>
              {/* Placeholders keep the column from jumping when the list lands;
                  they carry no text, so they are hidden from assistive tech. */}
              <ul className="sidebar__list" aria-hidden="true">
                <li className="sidebar__skeleton" />
                <li className="sidebar__skeleton" />
                <li className="sidebar__skeleton" />
              </ul>
            </>
          ) : conversations.length === 0 ? (
            <p className="sidebar__empty">هنوز گفتگویی ندارید. اولین سؤال را بپرسید.</p>
          ) : (
            <ul className="sidebar__list">
              {conversations.map((conversation) => {
                const isActive = conversation.id === activeId;
                const stored = conversation.title?.trim() ? conversation.title.trim() : "";
                const title = stored || "گفتگوی بدون عنوان";
                const stamp = formatStamp(conversation.updated_at);
                const isRenaming = renamingId === conversation.id;
                const menuOpen = menuId === conversation.id;

                if (isRenaming) {
                  return (
                    <li key={conversation.id} className="conv-row">
                      <form
                        className="conv-form"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void commitRename(conversation.id);
                        }}
                      >
                        <input
                          className="conv__input"
                          dir="auto"
                          autoFocus
                          maxLength={200}
                          value={draft}
                          aria-label="عنوان جدید گفتگو"
                          onChange={(event) => setDraft(event.target.value)}
                          onBlur={() => {
                            // Blur commits, like every other inline editor the user
                            // meets: losing the edit because they clicked away is worse
                            // than saving a title they can rename again.
                            void commitRename(conversation.id);
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Escape") {
                              event.preventDefault();
                              setRenamingId(null);
                            }
                          }}
                        />
                      </form>
                    </li>
                  );
                }

                return (
                  <li key={conversation.id} className="conv-row">
                    <button
                      type="button"
                      className={isActive ? "conv conv--active" : "conv"}
                      aria-current={isActive ? "page" : undefined}
                      title={title}
                      onClick={() => {
                        onSelect(conversation.id);
                        // On a phone the list is a drawer: selecting a thread
                        // must reveal it instead of leaving the drawer in place.
                        if (open) onClose();
                      }}
                    >
                      <span className="conv__title" dir="auto">
                        {title}
                      </span>
                      {stamp ? (
                        <span className="conv__stamp" aria-hidden="true">
                          {stamp}
                        </span>
                      ) : null}
                    </button>

                    <button
                      type="button"
                      className="conv__more"
                      aria-haspopup="menu"
                      aria-expanded={menuOpen}
                      aria-label={`گزینه‌های «${title}»`}
                      disabled={busyId === conversation.id}
                      onClick={() => {
                        setConfirmingId(null);
                        setMenuId(menuOpen ? null : conversation.id);
                      }}
                    >
                      ⋯
                    </button>

                    {menuOpen ? (
                      <div className="conv__menu" role="menu">
                        <button
                          type="button"
                          role="menuitem"
                          className="conv__menu-item"
                          onClick={() => {
                            setMenuId(null);
                            setConfirmingId(null);
                            setDraft(stored);
                            setRenamingId(conversation.id);
                          }}
                        >
                          تغییر نام
                        </button>
                        {confirmingId === conversation.id ? (
                          <>
                            <button
                              type="button"
                              role="menuitem"
                              className="conv__menu-item conv__menu-item--danger"
                              onClick={() => {
                                setMenuId(null);
                                setConfirmingId(null);
                                void commitDelete(conversation.id);
                              }}
                            >
                              حذف قطعی
                            </button>
                            <button
                              type="button"
                              role="menuitem"
                              className="conv__menu-item"
                              onClick={() => setConfirmingId(null)}
                            >
                              انصراف
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            role="menuitem"
                            className="conv__menu-item conv__menu-item--danger"
                            onClick={() => setConfirmingId(conversation.id)}
                          >
                            حذف
                          </button>
                        )}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </nav>

        <div className="sidebar__foot">
          <div className="sidebar__account">
            <span className="sidebar__account-label">حساب</span>
            <span className="sidebar__email ltr" title={email ?? undefined}>
              {email ?? "—"}
            </span>
          </div>
          <button type="button" className="sidebar__signout" onClick={onSignOut}>
            <SignOutIcon />
            <span>خروج</span>
          </button>
        </div>
      </aside>

      {/* A real button, not a div: on touch layouts this scrim is the only way
          back to the chat, so it has to be reachable by keyboard as well. */}
      <button
        type="button"
        className={open ? "sidebar__scrim sidebar__scrim--open" : "sidebar__scrim"}
        aria-label="بستن فهرست گفتگوها"
        tabIndex={open ? 0 : -1}
        onClick={onClose}
      />
    </>
  );
}
