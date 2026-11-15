import type { Conversation } from "../../services/api";

interface ConversationSidebarProps {
  conversations: Conversation[];
  activeConversationId: string | null;
  onSelect: (conversationId: string) => void;
  onNewConversation: () => void;
  isCreating: boolean;
  isLoading: boolean;
  email: string | null;
  onSignOut: () => void;
  isSigningOut: boolean;
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleDateString("fa-IR");
}

export function ConversationSidebar({
  conversations,
  activeConversationId,
  onSelect,
  onNewConversation,
  isCreating,
  isLoading,
  email,
  onSignOut,
  isSigningOut,
}: ConversationSidebarProps) {
  return (
    <div className="chat-sidebar__inner">
      <div className="chat-sidebar__head">
        <p className="chat-sidebar__brand">دستیار سازمانی</p>
        <button
          type="button"
          className="chat-sidebar__new"
          onClick={onNewConversation}
          disabled={isCreating}
        >
          {isCreating ? "در حال ساخت…" : "+ گفتگوی جدید"}
        </button>
      </div>

      <nav className="chat-sidebar__list" aria-label="گفتگوها">
        {isLoading ? (
          <p className="chat-sidebar__note" role="status">
            در حال بارگذاری گفتگوها…
          </p>
        ) : conversations.length === 0 ? (
          <p className="chat-sidebar__note">هنوز گفتگویی ندارید. اولین سؤال را بپرسید.</p>
        ) : (
          <ul className="chat-conv-list">
            {conversations.map((conversation) => {
              const isActive = conversation.id === activeConversationId;
              const date = formatDate(conversation.updated_at);

              return (
                <li key={conversation.id}>
                  <button
                    type="button"
                    className={isActive ? "chat-conv is-active" : "chat-conv"}
                    aria-current={isActive ? "true" : undefined}
                    onClick={() => {
                      onSelect(conversation.id);
                    }}
                  >
                    <span className="chat-conv__title" dir="auto">
                      {conversation.title?.trim() ? conversation.title : "بدون عنوان"}
                    </span>
                    {date ? <span className="chat-conv__date">{date}</span> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>

      <div className="chat-sidebar__foot">
        <p className="chat-sidebar__account" title={email ?? undefined}>
          <span className="chat-sidebar__account-label">حساب</span>
          <span className="chat-ltr chat-sidebar__email" dir="ltr">
            {email ?? "—"}
          </span>
        </p>
        <button type="button" className="chat-sidebar__signout" onClick={onSignOut} disabled={isSigningOut}>
          {isSigningOut ? "در حال خروج…" : "خروج"}
        </button>
      </div>
    </div>
  );
}
